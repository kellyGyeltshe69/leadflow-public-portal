from __future__ import annotations

import logging
import re
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from .browser_audit import audit_mobile_browser
from .config import get_settings
from .schemas import AuditResult
from .url_safety import public_http_url
from .utils import domain_of, extract_public_emails, normalize_url

log = logging.getLogger(__name__)
SOCIAL_DOMAINS = {"facebook.com", "instagram.com", "linkedin.com", "tiktok.com", "x.com", "twitter.com"}
REDIRECT_CODES = {301, 302, 303, 307, 308}


def _is_social(url: str) -> bool:
    host = domain_of(url)
    return any(host == item or host.endswith("." + item) for item in SOCIAL_DOMAINS)


def _attr_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return ""


def _safe_request(
    url: str,
    *,
    method: str = "GET",
    timeout: float = 15,
    max_bytes: int = 2_000_000,
    headers: dict[str, str] | None = None,
    max_redirects: int = 5,
) -> tuple[str, bytes, int, int, dict[str, str]] | None:
    """Fetch only public HTTP(S) destinations and validate every redirect.

    This reduces SSRF exposure from discovered URLs and redirect chains. It does
    not attempt to bypass authentication, bot protection, or robots.txt.
    """
    current = normalize_url(url)
    if not current:
        return None
    settings = get_settings()
    request_headers = {"User-Agent": settings.audit_user_agent, **(headers or {})}
    started = time.perf_counter()
    try:
        with httpx.Client(follow_redirects=False, timeout=timeout) as client:
            for _ in range(max_redirects + 1):
                if not public_http_url(current, resolve_dns=True):
                    log.warning("Blocked non-public audit URL or redirect: %s", current)
                    return None
                with client.stream(method, current, headers=request_headers) as response:
                    status = response.status_code
                    response_headers = dict(response.headers)
                    if status in REDIRECT_CODES and response.headers.get("location"):
                        current = urljoin(str(response.url), response.headers["location"])
                        continue
                    body = b""
                    if method != "HEAD" and max_bytes > 0:
                        chunks = []
                        total = 0
                        for chunk in response.iter_bytes():
                            if not chunk:
                                continue
                            remaining = max_bytes - total
                            chunks.append(chunk[:remaining])
                            total += min(len(chunk), remaining)
                            if total >= max_bytes:
                                break
                        body = b"".join(chunks)
                    elapsed_ms = round((time.perf_counter() - started) * 1000)
                    return str(response.url), body, status, elapsed_ms, response_headers
            log.info("Too many redirects while auditing %s", url)
            return None
    except (httpx.HTTPError, ValueError, OSError) as exc:
        log.info("Safe fetch failed for %s: %s", url, exc)
        return None


@lru_cache(maxsize=256)
def _robots_parser(origin: str) -> urllib.robotparser.RobotFileParser | None:
    settings = get_settings()
    robots_url = origin.rstrip("/") + "/robots.txt"
    result = _safe_request(robots_url, timeout=min(settings.audit_timeout_seconds, 8), max_bytes=512_000)
    if not result:
        return None
    _, body, status, _, _ = result
    if status >= 400:
        return None
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(body.decode("utf-8", errors="replace").splitlines())
    return parser


def robots_allowed(url: str) -> bool:
    settings = get_settings()
    if not settings.respect_robots_txt:
        return True
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    parser = _robots_parser(f"{parsed.scheme}://{parsed.netloc}")
    return True if parser is None else parser.can_fetch(settings.audit_user_agent, url)


def fetch_page_if_allowed(url: str) -> tuple[str, str, int, int] | None:
    settings = get_settings()
    normalized = normalize_url(url)
    if not normalized or _is_social(normalized) or not public_http_url(normalized, resolve_dns=True) or not robots_allowed(normalized):
        return None
    result = _safe_request(
        normalized,
        timeout=settings.audit_timeout_seconds,
        max_bytes=2_000_000,
        headers={"Accept": "text/html,application/xhtml+xml"},
    )
    if not result:
        return None
    final_url, body, status, elapsed_ms, headers = result
    content_type = headers.get("content-type", "")
    if "html" not in content_type.lower() and content_type:
        return final_url, "", status, elapsed_ms
    return final_url, body.decode("utf-8", errors="replace"), status, elapsed_ms


def _check_internal_link(link: str) -> dict:
    if not robots_allowed(link):
        return {"url": link, "status": None, "classification": "robots_disallowed"}
    result = _safe_request(link, method="HEAD", timeout=8, max_bytes=0)
    if not result:
        return {"url": link, "status": None, "classification": "inconclusive"}
    final_url, _, status, elapsed_ms, _ = result
    if status in {403, 405}:
        result = _safe_request(
            link,
            method="GET",
            timeout=8,
            max_bytes=1024,
            headers={"Range": "bytes=0-1023"},
        )
        if not result:
            return {"url": link, "status": status, "classification": "inconclusive", "elapsed_ms": elapsed_ms}
        final_url, _, status, elapsed_ms, _ = result
    if status in {404, 410}:
        classification = "broken"
    elif status >= 500:
        classification = "server_error_retest"
    elif status >= 400:
        classification = "protected_or_inconclusive"
    else:
        classification = "ok"
    return {"url": link, "final_url": final_url, "status": status, "elapsed_ms": elapsed_ms, "classification": classification}


def _sample_internal_links(base_url: str, soup: BeautifulSoup) -> list[dict]:
    settings = get_settings()
    limit = settings.audit_link_check_limit
    if limit <= 0:
        return []
    links: list[str] = []
    for anchor in soup.select("a[href]"):
        href = _attr_text(anchor.get("href")).strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            continue
        candidate = urljoin(base_url, href)
        if (
            domain_of(candidate) == domain_of(base_url)
            and public_http_url(candidate, resolve_dns=False)
            and candidate not in links
        ):
            links.append(candidate)
        if len(links) >= limit:
            break
    results = []
    with ThreadPoolExecutor(max_workers=min(settings.audit_link_workers, len(links) or 1)) as executor:
        futures = {executor.submit(_check_internal_link, link): link for link in links}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append({"url": futures[future], "status": None, "classification": "inconclusive", "error": str(exc)[:300]})
    return sorted(results, key=lambda item: links.index(item["url"]))


def _add_issue(issues: list[str], confidence: dict[str, str], text: str, level: str) -> None:
    if text not in issues:
        issues.append(text)
    confidence[text] = level


def audit_website(url: str | None) -> AuditResult:
    if not url:
        issue = "No independent website was found in the permitted public-data workflow."
        return AuditResult(
            website_status="no_website_found",
            facts={"audit_scope": "No independent website URL was supplied or discovered.", "issue_confidence": {issue: "medium"}},
            issues=[issue],
        )
    url = normalize_url(url)
    if not url or not public_http_url(url, resolve_dns=True):
        return AuditResult(
            website_status="blocked_unsafe_url",
            facts={"url": url, "security_block": "URL was not a verified public HTTP(S) destination."},
            issues=[],
            evidence=[("security", "The audit refused a non-public, credentialed, internal, or non-web destination.", url or "")],
        )
    if _is_social(url):
        issue = "The business appears to rely on a social profile rather than an independent website."
        return AuditResult(
            website_status="social_only",
            facts={"primary_url": url, "issue_confidence": {issue: "high"}},
            issues=[issue],
            evidence=[("social_only", "The primary public URL is a social-media profile.", url)],
        )
    if not robots_allowed(url):
        return AuditResult(
            website_status="not_audited_robots",
            facts={"url": url, "robots_allowed": False},
            issues=[],
            evidence=[("robots", "The site disallowed this automated audit user-agent; no page content was fetched.", url)],
        )

    page = fetch_page_if_allowed(url)
    if not page:
        issue = "The website was unreachable from the audit VPS. Recheck manually before mentioning this."
        return AuditResult(
            website_status="unreachable",
            facts={"url": url, "issue_confidence": {issue: "low"}},
            issues=[issue],
            evidence=[("site_check", "The first-party site could not be fetched during the audit.", url)],
        )

    final_url, html, status_code, elapsed_ms = page
    facts = {
        "requested_url": url,
        "final_url": final_url,
        "http_status": status_code,
        "response_elapsed_ms_from_vps": elapsed_ms,
        "https": final_url.lower().startswith("https://"),
        "html_bytes_sampled": len(html.encode("utf-8", errors="ignore")),
    }
    issues: list[str] = []
    confidence: dict[str, str] = {}
    evidence = [("site_check", f"Site returned HTTP {status_code} in {elapsed_ms} ms from the audit VPS.", final_url)]

    if status_code >= 400:
        _add_issue(issues, confidence, f"The first-party website returned HTTP {status_code} during the audit.", "high")
    if not facts["https"]:
        _add_issue(issues, confidence, "The final website URL did not use HTTPS.", "high")
    if elapsed_ms > 2500:
        _add_issue(
            issues,
            confidence,
            f"A slow server response ({elapsed_ms} ms) was observed from the audit VPS; retest before using this claim.",
            "low",
        )

    soup = BeautifulSoup(html, "html.parser") if html else BeautifulSoup("", "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    description_tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.IGNORECASE)})
    viewport = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.IGNORECASE)})
    body_text = " ".join(soup.get_text(" ", strip=True).split())[:10000]
    body_lower = body_text.lower()
    visible_emails = extract_public_emails(body_text)
    facts.update({
        "title": title,
        "meta_description_present": bool(isinstance(description_tag, Tag) and _attr_text(description_tag.get("content")).strip()),
        "viewport_present": bool(viewport),
        "public_email_count": len(visible_emails),
        "form_count": len(soup.find_all("form")),
        "body_text_excerpt": body_text[:1200],
    })
    if not title:
        _add_issue(issues, confidence, "The homepage did not expose a usable HTML title.", "high")
    if not viewport:
        _add_issue(issues, confidence, "No mobile viewport meta tag was detected in the homepage HTML.", "high")
    if not facts["meta_description_present"]:
        _add_issue(issues, confidence, "No homepage meta description was detected.", "high")
    if any(term in (title + " " + body_lower) for term in ["website expired", "domain for sale", "account suspended"]):
        _add_issue(issues, confidence, "The website displayed an expired, parked or suspended-site message.", "high")
        website_status = "expired_or_parked"
    elif status_code >= 400:
        website_status = "broken_http_error"
    else:
        website_status = "active_with_gaps" if issues else "active_no_major_issue_verified"

    link_results = _sample_internal_links(final_url, soup)
    facts["sample_internal_link_results"] = link_results
    broken = [item for item in link_results if item.get("classification") == "broken"]
    server_errors = [item for item in link_results if item.get("classification") == "server_error_retest"]
    if broken:
        _add_issue(issues, confidence, f"A {len(link_results)}-link internal sample found {len(broken)} broken link(s) returning 404/410.", "high")
    if server_errors:
        _add_issue(issues, confidence, f"A {len(link_results)}-link internal sample found {len(server_errors)} server-error link(s); retest manually.", "medium")

    contact_links = []
    for anchor in soup.select("a[href]"):
        href = _attr_text(anchor.get("href"))
        label = (anchor.get_text(" ", strip=True) + " " + href).lower()
        if any(word in label for word in ["contact", "book", "inquir", "quote", "reservation"]):
            contact_links.append(urljoin(final_url, href))
    facts["contact_link_present"] = bool(contact_links)
    if not contact_links and not visible_emails:
        _add_issue(issues, confidence, "No obvious contact link or public email was detected on the homepage HTML.", "high")

    browser = audit_mobile_browser(final_url)
    if browser.facts:
        facts["mobile_browser"] = browser.facts
        if browser.facts.get("screenshot_path"):
            facts["screenshot_path"] = browser.facts["screenshot_path"]
    for issue in browser.issues:
        level = (browser.facts.get("issue_confidence") or {}).get(issue, "medium")
        _add_issue(issues, confidence, issue, level)
    evidence.extend(browser.evidence)
    facts["issue_confidence"] = confidence
    if website_status.startswith("active") and issues:
        website_status = "active_with_gaps"
    return AuditResult(website_status=website_status, facts=facts, issues=issues, evidence=evidence)
