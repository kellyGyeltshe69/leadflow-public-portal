from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DiscoveredBusiness:
    place_id: str | None
    name: str
    industry: str
    city: str
    state: str
    formatted_address: str = ""
    website_url: str | None = None
    maps_url: str | None = None
    source_url: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    provider: str = "unknown"
    google_rating: float | None = None
    google_review_count: int | None = None
    growth_signals: list[str] = field(default_factory=list)


@dataclass
class ContactResult:
    email: str | None = None
    contact_page: str | None = None
    website_url: str | None = None
    evidence: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass
class AuditResult:
    website_status: str
    facts: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    evidence: list[tuple[str, str, str]] = field(default_factory=list)
