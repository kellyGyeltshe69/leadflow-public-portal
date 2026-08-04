from __future__ import annotations

import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, cast
from urllib.parse import urljoin, urlparse

import dns.resolver
from bs4 import BeautifulSoup, Tag

from ..config import get_settings
from ..schemas import AuditResult
from ..url_safety import public_http_url
from ..utils import normalize_url
from ..webaudit import _safe_request, audit_website, fetch_page_if_allowed


class AdvancedAuditService:
    """Evidence-based extensions to the compatibility website audit."""

    def _tls(self, url: str) -> dict:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or not public_http_url(url):
            return {"ssl_present": False, "ssl_expired": None, "ssl_days_remaining": None}
        try:
            context = ssl.create_default_context()
            with (
                socket.create_connection((parsed.hostname, 443), timeout=8) as raw,
                context.wrap_socket(raw, server_hostname=parsed.hostname) as secured,
            ):
                certificate = cast(dict[str, Any], secured.getpeercert() or {})
            not_after = str(certificate.get("notAfter") or "")
            expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            days = (expires - datetime.now(timezone.utc)).days
            issuer = {}
            for group in certificate.get("issuer", ()):
                for key, value in group:
                    issuer[str(key)] = str(value)
            return {
                "ssl_present": True,
                "ssl_expired": days < 0,
                "ssl_days_remaining": days,
                "ssl_issuer": issuer,
            }
        except Exception as exc:
            return {"ssl_present": True, "ssl_error": str(exc)[:300], "ssl_expired": None}

    def _dns(self, hostname: str) -> dict:
        record_types = ["A", "AAAA", "CNAME", "MX", "NS", "TXT"]

        def resolve(record_type: str) -> tuple[str, list[str]]:
            try:
                answers = dns.resolver.resolve(hostname, record_type, lifetime=5)
                values = [str(answer).rstrip(".")[:500] for answer in answers][:20]
            except Exception:
                values = []
            return record_type.lower(), values

        # The record types are independent. Resolve them concurrently so one
        # missing type does not add another five-second delay to every lead.
        with ThreadPoolExecutor(max_workers=len(record_types)) as executor:
            pairs = list(executor.map(resolve, record_types))
        return dict(pairs)

    @staticmethod
    def _technology(html: str, headers: dict[str, str]) -> dict:
        lower = html.lower()
        soup = BeautifulSoup(html, "html.parser")
        generator = ""
        generator_tag = soup.find("meta", attrs={"name": "generator"})
        if isinstance(generator_tag, Tag):
            generator = str(generator_tag.get("content") or "")[:200]
        technologies = []
        signatures = {
            "WordPress": ["wp-content", "wp-includes"],
            "Wix": ["wixstatic.com", "wix-code"],
            "Squarespace": ["static1.squarespace.com", "squarespace"],
            "Shopify": ["cdn.shopify.com", "shopify.theme"],
            "Webflow": ["webflow.css", "webflow.js"],
            "Next.js": ["/_next/", "__next_data__"],
            "React": ["react-dom", "data-reactroot"],
        }
        for name, needles in signatures.items():
            if any(needle in lower for needle in needles):
                technologies.append(name)
        server = headers.get("server", "")[:200]
        powered_by = headers.get("x-powered-by", "")[:200]
        wordpress_version = None
        if generator.lower().startswith("wordpress"):
            wordpress_version = generator.partition(" ")[2] or None
        return {
            "technologies": technologies,
            "generator": generator,
            "server_header": server,
            "powered_by": powered_by,
            "cms": technologies[0] if technologies and technologies[0] in {"WordPress", "Wix", "Squarespace", "Shopify", "Webflow"} else None,
            "wordpress_version_exposed": wordpress_version,
        }

    def audit_result(self, url: str | None) -> AuditResult:
        data = self.audit(url)
        return AuditResult(
            website_status=data["website_status"],
            facts=data["facts"],
            issues=data["issues"],
            evidence=data["evidence"],
        )

    def audit(self, url: str | None) -> dict:
        basic = audit_website(url)
        result: dict[str, Any] = {
            "website_status": basic.website_status,
            "facts": dict(basic.facts or {}),
            "issues": list(basic.issues or []),
            "evidence": list(basic.evidence or []),
        }
        normalized = normalize_url(url)
        if not normalized or not public_http_url(normalized):
            return result
        page = fetch_page_if_allowed(normalized)
        if not page:
            return result
        final_url, html, _, _ = page
        raw = _safe_request(final_url, method="HEAD", max_bytes=0)
        headers = raw[4] if raw else {}
        soup = BeautifulSoup(html, "html.parser")
        parsed = urlparse(final_url)

        favicon = soup.find("link", rel=lambda value: value and "icon" in str(value).lower())
        favicon_url = urljoin(final_url, str(favicon.get("href"))) if isinstance(favicon, Tag) and favicon.get("href") else None
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
        robots = _safe_request(robots_url, max_bytes=256_000)
        sitemap = _safe_request(sitemap_url, max_bytes=256_000)

        image_sources = list(dict.fromkeys(
            urljoin(final_url, str(image.get("src")))
            for image in soup.select("img[src]")[:20]
        ))

        def probe_image(source: str) -> dict[str, Any]:
            probe = _safe_request(source, method="HEAD", max_bytes=0, timeout=6)
            size = None
            if probe:
                try:
                    size = int(probe[4].get("content-length", ""))
                except ValueError:
                    size = None
            return {"url": source[:500], "bytes": size}

        images: list[dict[str, Any]] = []
        if image_sources:
            workers = max(1, min(get_settings().audit_link_workers, 8, len(image_sources)))
            # Image metadata probes are independent and bounded. Running a
            # small pool preserves the same evidence while avoiding up to 20
            # sequential six-second waits on slow sites.
            with ThreadPoolExecutor(max_workers=workers) as executor:
                images = list(executor.map(probe_image, image_sources))
        total_known_bytes = sum(int(image["bytes"] or 0) for image in images)

        dns_records = self._dns(parsed.hostname or "")
        technology = self._technology(html, headers)
        tls = self._tls(final_url)
        cname_text = " ".join(dns_records.get("cname", [])).lower()
        hosting_hint = next(
            (name for name, needle in {
                "Cloudflare": "cloudflare",
                "Vercel": "vercel",
                "Netlify": "netlify",
                "GitHub Pages": "github.io",
                "AWS": "amazonaws",
            }.items() if needle in cname_text),
            None,
        )

        facts: dict[str, Any] = result["facts"]
        facts.update({
            **tls,
            "favicon_present": bool(favicon_url),
            "favicon_url": favicon_url,
            "robots_txt_status": robots[2] if robots else None,
            "sitemap_status": sitemap[2] if sitemap else None,
            "sitemap_present": bool(sitemap and sitemap[2] == 200),
            "image_sample_count": len(images),
            "image_sample_known_bytes": total_known_bytes,
            "large_image_count": sum(1 for image in images if (image.get("bytes") or 0) > 500_000),
            "image_samples": images,
            "dns": dns_records,
            "technology_stack": technology,
            "hosting_provider_hint": hosting_hint,
            "domain_age": None,
            "domain_authority": None,
            "traffic_estimate": None,
            "unavailable_metrics_reason": "Domain age, authority, and traffic require licensed third-party datasets.",
        })
        issues: list[str] = result["issues"]
        if tls.get("ssl_expired") is True:
            issues.append("The TLS certificate appears expired.")
        elif tls.get("ssl_days_remaining") is not None and tls["ssl_days_remaining"] < 21:
            issues.append(f"The TLS certificate expires in approximately {tls['ssl_days_remaining']} days.")
        if not favicon_url:
            issues.append("No favicon link was detected in the homepage HTML.")
        if not facts["sitemap_present"]:
            issues.append("No standard /sitemap.xml response was verified.")
        if facts["large_image_count"]:
            issues.append(f"The homepage image sample found {facts['large_image_count']} image(s) larger than 500 KB.")
        if technology.get("wordpress_version_exposed"):
            issues.append("The WordPress generator tag exposes a version; verify whether disclosure is necessary.")

        performance = 100
        performance -= min(35, facts["large_image_count"] * 8)
        if facts.get("response_elapsed_ms_from_vps", 0) > 2500:
            performance -= 25
        seo = 100
        for present in [facts.get("title"), facts.get("meta_description_present"), facts.get("sitemap_present")]:
            if not present:
                seo -= 20
        mobile = 100
        if not facts.get("viewport_present"):
            mobile -= 40
        overflow = ((facts.get("mobile_browser") or {}).get("horizontalOverflowPixels") or 0)
        if overflow > 8:
            mobile -= 30
        facts["scores"] = {
            "performance": max(0, performance),
            "seo": max(0, seo),
            "mobile": max(0, mobile),
            "security": 100 if tls.get("ssl_present") and tls.get("ssl_expired") is False else 40,
        }
        result["issues"] = list(dict.fromkeys(issues))
        return result
