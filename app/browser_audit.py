from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .config import get_settings
from .schemas import AuditResult
from .url_safety import public_http_url

log = logging.getLogger(__name__)


def _redact_url_query(url: str) -> str:
    try:
        parsed = urlsplit(url)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))[:500]
    except ValueError:
        return "[invalid URL]"


def audit_mobile_browser(url: str) -> AuditResult:
    settings = get_settings()
    if not settings.browser_audit_enabled:
        return AuditResult(website_status="browser_audit_disabled")
    if not public_http_url(url, resolve_dns=True):
        return AuditResult(
            website_status="browser_audit_blocked_url",
            facts={"browser_audit_blocked": True},
            issues=[],
            evidence=[("browser_security", "Mobile rendering was blocked because the URL was not a verified public HTTP(S) destination.", url)],
        )
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return AuditResult(
            website_status="browser_dependency_missing",
            facts={"browser_audit_available": False},
            evidence=[("browser_audit", "Playwright is not installed; the HTTP/HTML audit still ran.", url)],
        )

    screenshot_dir = Path(settings.browser_screenshot_dir).expanduser().resolve()
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = screenshot_dir / f"{hashlib.sha256(url.encode()).hexdigest()[:24]}.png"
    console_errors: list[None] = []
    failed_requests: list[str] = []
    issues: list[str] = []
    confidence: dict[str, str] = {}
    facts: dict = {
        "browser_audit_available": True,
        "mobile_viewport": [settings.browser_viewport_width, settings.browser_viewport_height],
    }

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
            context = browser.new_context(
                viewport={"width": settings.browser_viewport_width, "height": settings.browser_viewport_height},
                device_scale_factor=1,
                is_mobile=True,
                has_touch=True,
                user_agent=(
                    "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124 Mobile Safari/537.36 LeadFlowAudit/0.2"
                ),
                locale="en-US",
            )

            def guard_route(route, request):
                # Resolve the main destination before launch. For subresources, block
                # private IP literals, internal suffixes, single-label service names,
                # credentials, and non-web ports without performing dozens of DNS lookups.
                if public_http_url(request.url, resolve_dns=request.resource_type == "document"):
                    route.continue_()
                else:
                    route.abort("blockedbyclient")

            context.route("**/*", guard_route)
            page = context.new_page()
            # Count console errors without retaining message text, which can
            # occasionally contain accidental tokens or customer data.
            page.on(
                "console",
                lambda message: console_errors.append(None) if message.type == "error" and len(console_errors) < 20 else None,
            )
            page.on(
                "requestfailed",
                lambda request: failed_requests.append(_redact_url_query(request.url)) if len(failed_requests) < 20 else None,
            )
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=settings.browser_audit_timeout_seconds * 1000,
            )
            page.wait_for_timeout(1200)
            metrics = page.evaluate(
                """() => {
                    const root = document.documentElement;
                    const bodyText = (document.body?.innerText || '').trim();
                    const interactive = [...document.querySelectorAll('a,button,input,select,textarea,[role="button"]')]
                      .filter(el => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                      });
                    const smallTargets = interactive.filter(el => {
                        const r = el.getBoundingClientRect();
                        return r.width < 24 || r.height < 24;
                    }).length;
                    return {
                        title: document.title || '',
                        finalUrl: location.href,
                        textCharacters: bodyText.length,
                        horizontalOverflowPixels: Math.max(0, root.scrollWidth - innerWidth),
                        interactiveElements: interactive.length,
                        smallInteractiveElements: smallTargets,
                        visibleForms: [...document.forms].filter(form => {
                            const r = form.getBoundingClientRect(); return r.width > 0 && r.height > 0;
                        }).length,
                        hasVisibleContactLink: [...document.querySelectorAll('a')].some(a => {
                            const r = a.getBoundingClientRect();
                            return r.width > 0 && r.height > 0 && /contact|book|quote|inquir|reserv/i.test((a.innerText || '') + ' ' + (a.href || ''));
                        })
                    };
                }"""
            )
            # Capture the initial mobile viewport only; unbounded full-page screenshots
            # can consume excessive memory on malicious or extremely long pages.
            page.screenshot(path=str(screenshot_path), full_page=False, animations="disabled")
            facts.update(metrics)
            facts.update({
                "browser_http_status": response.status if response else None,
                "browser_console_error_count": len(console_errors),
                "browser_failed_request_count": len(failed_requests),
                "browser_failed_request_samples_without_queries": failed_requests[:5],
                "screenshot_path": str(screenshot_path),
            })
            if metrics.get("horizontalOverflowPixels", 0) > 8:
                issue = f"Mobile rendering showed {metrics['horizontalOverflowPixels']} px of horizontal overflow at {settings.browser_viewport_width}px width."
                issues.append(issue)
                confidence[issue] = "high"
            if metrics.get("textCharacters", 0) < 80:
                issue = "The rendered mobile page exposed very little visible text; manually confirm whether content failed to load."
                issues.append(issue)
                confidence[issue] = "medium"
            if metrics.get("interactiveElements", 0) >= 5 and metrics.get("smallInteractiveElements", 0) / max(metrics["interactiveElements"], 1) > 0.5:
                issue = "More than half of the visible interactive elements rendered below 24 px in at least one dimension; review mobile tap targets manually."
                issues.append(issue)
                confidence[issue] = "medium"
            context.close()
            browser.close()
    except PlaywrightTimeoutError:
        # A timeout is a limitation of this audit location, not evidence that the
        # business site is slow, so it is recorded without increasing lead need.
        facts["browser_timeout"] = True
    except PlaywrightError as exc:
        log.info("Playwright audit failed for %s: %s", url, exc)
        facts["browser_error"] = str(exc)[:1000]
    except Exception as exc:
        log.warning("Unexpected mobile browser audit error for %s: %s", url, exc)
        facts["browser_error"] = str(exc)[:1000]

    facts["issue_confidence"] = confidence
    screenshot_exists = screenshot_path.exists()
    evidence = [
        (
            "mobile_browser",
            "Rendered the first-party page in a 390px-class touch viewport and captured observable layout facts."
            if screenshot_exists
            else "Attempted a mobile Chromium render; see limitations in the recorded browser facts.",
            url,
        )
    ]
    return AuditResult(
        website_status="browser_rendered" if screenshot_exists else "browser_render_inconclusive",
        facts=facts,
        issues=issues,
        evidence=evidence,
    )
