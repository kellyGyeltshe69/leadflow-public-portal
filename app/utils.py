from __future__ import annotations

import re
from email.utils import parseaddr
from urllib.parse import urlparse

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)


def normalize_business_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def normalize_email(value: str) -> str:
    return parseaddr(value or "")[1].strip().lower()


def normalize_url(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value


def domain_of(value: str | None) -> str:
    if not value:
        return ""
    try:
        return urlparse(value).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def extract_public_emails(text: str) -> list[str]:
    blocked_fragments = ("sentry", "example.com", "wixpress", "cloudflare", "noreply", "no-reply")
    result = []
    for raw in EMAIL_RE.findall(text or ""):
        email = normalize_email(raw.rstrip(".,;:)"))
        if not email or any(fragment in email for fragment in blocked_fragments):
            continue
        if email not in result:
            result.append(email)
    return result
