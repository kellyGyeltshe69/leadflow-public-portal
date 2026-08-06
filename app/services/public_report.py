from __future__ import annotations

from urllib.parse import urlparse


def _bounded_score(value: object) -> int | None:
    if not isinstance(value, (int, float)):
        return None
    return max(0, min(100, round(float(value))))


def _severity(issue: str) -> str:
    lower = issue.lower()
    if any(term in lower for term in ("expired", "unsafe", "broken", "http 5", "suspended")):
        return "high"
    if any(term in lower for term in ("slow", "larger than 500", "meta description", "viewport", "https")):
        return "medium"
    return "advisory"


def _category(issue: str) -> str:
    lower = issue.lower()
    if any(term in lower for term in ("ssl", "tls", "https", "security")):
        return "Security"
    if any(term in lower for term in ("meta", "title", "sitemap", "robots", "search")):
        return "Search presentation"
    if any(term in lower for term in ("mobile", "viewport", "tap", "overflow")):
        return "Mobile"
    if any(term in lower for term in ("image", "slow", "response", "performance")):
        return "Performance"
    return "Customer journey"


def _technology_names(facts: dict) -> str:
    stack = facts.get("technology_stack") or {}
    if isinstance(stack, dict):
        technologies = stack.get("technologies") or []
        if isinstance(technologies, list):
            return ", ".join(str(item) for item in technologies[:5])
    return ""


def build_public_report(lead, business, landing, content: dict) -> dict[str, object]:
    facts = dict(content.get("facts") or (lead.audit_facts or {}))
    issues = list(content.get("issues") or (lead.issues or []))[:8]
    confidence_map = facts.get("issue_confidence") or {}
    if not isinstance(confidence_map, dict):
        confidence_map = {}

    findings = [
        {
            "text": str(issue),
            "severity": _severity(str(issue)),
            "category": _category(str(issue)),
            "confidence": str(confidence_map.get(str(issue), "medium")),
        }
        for issue in issues
    ]
    raw_scores = facts.get("scores") or {}
    if not isinstance(raw_scores, dict):
        raw_scores = {}
    score_labels = {
        "performance": "Performance",
        "seo": "SEO",
        "mobile": "Mobile",
        "security": "Security",
    }
    scores = [
        {"name": label, "value": _bounded_score(raw_scores.get(name))}
        for name, label in score_labels.items()
    ]

    checklist = list(content.get("checklist") or (lead.checklist or []))[:6]
    if not checklist:
        checklist = [
            "Verify the findings against the current live website.",
            "Fix the highest-priority customer-impact issue first.",
            "Retest on a phone and a desktop connection after changes.",
        ]

    website_url = str(lead.website_url or business.website_url or "")
    try:
        website_host = (urlparse(website_url).hostname or "").removeprefix("www.")
    except ValueError:
        website_host = ""
    response_ms = facts.get("response_elapsed_ms_from_vps")
    technical: list[dict[str, object]] = [
        {"label": "HTTPS", "value": "Verified" if facts.get("https") else "Not verified"},
        {"label": "HTTP status", "value": facts.get("http_status")},
        {"label": "Server response", "value": f"{response_ms} ms" if isinstance(response_ms, (int, float)) else None},
        {"label": "Large images", "value": facts.get("large_image_count")},
        {"label": "Mobile viewport", "value": "Detected" if facts.get("viewport_present") else "Not detected"},
        {"label": "Meta description", "value": "Detected" if facts.get("meta_description_present") else "Not detected"},
        {"label": "Sitemap", "value": "Detected" if facts.get("sitemap_present") else "Not verified"},
        {"label": "Technology", "value": _technology_names(facts)},
    ]
    technical = [item for item in technical if item["value"] not in {None, ""}]

    overall = _bounded_score(getattr(business, "website_score", None)) or 0
    high_count = sum(1 for finding in findings if finding["severity"] == "high")
    medium_count = sum(1 for finding in findings if finding["severity"] == "medium")
    summary = (
        f"This point-in-time review recorded {len(findings)} item(s): "
        f"{high_count} high priority and {medium_count} medium priority. "
        "Verify each observation on the current site before making a platform or hosting decision."
    )
    return {
        "business_name": business.name,
        "industry": lead.industry,
        "location": ", ".join(item for item in (lead.city, lead.state) if item),
        "website_url": website_url,
        "website_host": website_host,
        "website_status": str(lead.website_status or "unknown").replace("_", " ").title(),
        "reviewed_at": lead.last_verified_at,
        "overall_score": overall,
        "summary": summary,
        "findings": findings,
        "scores": scores,
        "checklist": [str(item) for item in checklist],
        "technical": technical,
        "opportunity": str(content.get("opportunity") or lead.opportunity or ""),
        "recommended_plan": landing.recommended_plan,
    }
