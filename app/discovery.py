from __future__ import annotations

import itertools
import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .composio_gateway import ComposioGateway
from .config import get_settings
from .schemas import ContactResult, DiscoveredBusiness
from .utils import (
    domain_of,
    extract_public_emails,
    normalize_business_name,
    normalize_url,
)
from .webaudit import fetch_page_if_allowed

log = logging.getLogger(__name__)

SOCIAL_DOMAINS = {"facebook.com", "instagram.com", "linkedin.com", "tiktok.com", "x.com", "twitter.com"}
DIRECTORY_DOMAINS = {
    "yelp.com", "mapquest.com", "yellowpages.com", "tripadvisor.com", "doordash.com",
    "ubereats.com", "grubhub.com", "roaminghunger.com", "chamberofcommerce.com",
}


def _attr_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return ""


def _address_part(parts: list[dict], wanted: str, short: bool = False) -> str:
    for part in parts or []:
        if wanted in part.get("types", []):
            return part.get("shortText" if short else "longText", "")
    return ""


class ComposioGoogleMapsProvider:
    endpoint = "https://places.googleapis.com/v1/places:searchText"

    def __init__(self) -> None:
        self.gateway = ComposioGateway()

    def search(self, query: str, limit: int = 10) -> list[DiscoveredBusiness]:
        field_mask = (
            "places.id,places.displayName,places.formattedAddress,places.addressComponents,"
            "places.websiteUri,places.googleMapsUri,places.primaryType,places.businessStatus,places.location,"
            "places.rating,places.userRatingCount"
        )
        payload = {"textQuery": query, "regionCode": "US", "pageSize": min(limit, 20)}
        data = self.gateway.proxy(
            "google_maps",
            self.endpoint,
            "POST",
            body=payload,
            parameters=[{"name": "X-Goog-FieldMask", "value": field_mask, "type": "header"}],
        )
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected Google Places response through Composio: {type(data).__name__}")
        businesses: list[DiscoveredBusiness] = []
        for place in data.get("places", []):
            if place.get("businessStatus") and place.get("businessStatus") != "OPERATIONAL":
                continue
            address = place.get("addressComponents") or []
            city = _address_part(address, "locality") or _address_part(address, "postal_town")
            state = _address_part(address, "administrative_area_level_1", short=True)
            location = place.get("location") or {}
            businesses.append(DiscoveredBusiness(
                place_id=place.get("id"),
                name=(place.get("displayName") or {}).get("text", "").strip(),
                industry=(place.get("primaryType") or "small_business").replace("_", " "),
                city=city,
                state=state,
                formatted_address=place.get("formattedAddress", ""),
                website_url=normalize_url(place.get("websiteUri")),
                maps_url=place.get("googleMapsUri"),
                source_url=place.get("googleMapsUri"),
                latitude=location.get("latitude"),
                longitude=location.get("longitude"),
                provider="composio_google_maps",
                google_rating=place.get("rating"),
                google_review_count=place.get("userRatingCount"),
                growth_signals=["Appeared in current Google Places results through Composio"],
            ))
        return [x for x in businesses if x.name][:limit]


class DemoDiscoveryProvider:
    """Synthetic records. Demo mode can never send email."""

    def search(self, query: str, limit: int = 10) -> list[DiscoveredBusiness]:
        fixtures = [
            DiscoveredBusiness("demo-001", "Sample Sunrise Bakery", "bakery", "Demo City", "CA", "101 Demo Ave", None, "https://maps.example.invalid/1", "https://example.invalid/demo-source-1", provider="demo", growth_signals=["Synthetic newly opened business"]),
            DiscoveredBusiness("demo-002", "Sample Paws Grooming", "pet groomer", "Demo Town", "TX", "202 Test St", "https://example.invalid/sample-paws", "https://maps.example.invalid/2", "https://example.invalid/demo-source-2", provider="demo", growth_signals=["Synthetic active business"]),
            DiscoveredBusiness("demo-003", "Sample Willow Events", "event venue", "Demo Village", "OH", "303 Example Rd", None, "https://maps.example.invalid/3", "https://example.invalid/demo-source-3", provider="demo", growth_signals=["Synthetic bookings signal"]),
        ]
        return fixtures[:limit]


def discovery_provider(demo_mode: bool | None = None):
    if demo_mode is None:
        demo_mode = get_settings().demo_mode
    return DemoDiscoveryProvider() if demo_mode else ComposioGoogleMapsProvider()


class SearchClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def search(self, query: str, limit: int = 8) -> list[dict]:
        provider = self.settings.search_provider.lower()
        if provider == "serper" and self.settings.serper_api_key:
            response = httpx.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": self.settings.serper_api_key, "Content-Type": "application/json"},
                json={"q": query, "gl": "us", "hl": "en", "num": limit}, timeout=30,
            )
            response.raise_for_status()
            return [
                {"title": x.get("title", ""), "url": x.get("link", ""), "snippet": x.get("snippet", "")}
                for x in response.json().get("organic", [])[:limit]
            ]
        if provider == "bing" and self.settings.bing_search_api_key:
            response = httpx.get(
                self.settings.bing_search_endpoint,
                headers={"Ocp-Apim-Subscription-Key": self.settings.bing_search_api_key},
                params={"q": query, "mkt": "en-US", "count": limit, "responseFilter": "Webpages"},
                timeout=30,
            )
            response.raise_for_status()
            return [
                {"title": x.get("name", ""), "url": x.get("url", ""), "snippet": x.get("snippet", "")}
                for x in (response.json().get("webPages") or {}).get("value", [])[:limit]
            ]
        if provider == "brave" and self.settings.brave_search_api_key:
            response = httpx.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": self.settings.brave_search_api_key, "Accept": "application/json"},
                params={"q": query, "country": "us", "search_lang": "en", "count": limit}, timeout=30,
            )
            response.raise_for_status()
            return [
                {"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("description", "")}
                for x in (response.json().get("web") or {}).get("results", [])[:limit]
            ]
        return []


def _title_matches_business(result: dict, business: DiscoveredBusiness) -> bool:
    title_norm = normalize_business_name(result.get("title", ""))
    name_norm = normalize_business_name(business.name)
    return bool(name_norm and (name_norm in title_norm or SequenceMatcher(None, name_norm, title_norm).ratio() >= 0.72))


def _likely_official(result: dict, business: DiscoveredBusiness) -> bool:
    url = result.get("url", "")
    host = domain_of(url)
    if not host or any(host == d or host.endswith("." + d) for d in SOCIAL_DOMAINS | DIRECTORY_DOMAINS):
        return False
    return _title_matches_business(result, business)


def _select_business_email(emails: list[str], website_url: str | None) -> str | None:
    if not emails:
        return None
    domain = domain_of(website_url)
    domain_matches = [e for e in emails if domain and e.split("@")[-1].lower().endswith(domain)]
    preferred = domain_matches or emails
    role_prefixes = ("info@", "hello@", "contact@", "office@", "bookings@", "events@", "sales@", "support@")
    preferred.sort(key=lambda e: (not e.lower().startswith(role_prefixes), len(e)))
    return preferred[0]


def contacts_from_website(url: str) -> ContactResult:
    result = ContactResult(website_url=url)
    page = fetch_page_if_allowed(url)
    if not page:
        return result
    final_url, html, _status, _elapsed = page
    soup = BeautifulSoup(html, "html.parser")
    # Use visible text and explicit mailto links, not script/config blobs.
    emails = extract_public_emails(soup.get_text(" ", strip=True))
    for a in soup.select("a[href]"):
        href = _attr_text(a.get("href"))
        if href.lower().startswith("mailto:"):
            emails.extend(extract_public_emails(href))
    contact_candidates = []
    for a in soup.select("a[href]"):
        href = _attr_text(a.get("href"))
        label = (a.get_text(" ", strip=True) + " " + href).lower()
        if any(word in label for word in ["contact", "book", "inquir", "event", "catering"]):
            candidate = urljoin(final_url, href)
            if domain_of(candidate) == domain_of(final_url) and candidate not in contact_candidates:
                contact_candidates.append(candidate)
    for contact_url in contact_candidates[:2]:
        contact_page = fetch_page_if_allowed(contact_url)
        if not contact_page:
            continue
        _, contact_html, _, _ = contact_page
        contact_soup = BeautifulSoup(contact_html, "html.parser")
        found = extract_public_emails(contact_soup.get_text(" ", strip=True))
        for anchor in contact_soup.select("a[href^='mailto:']"):
            found.extend(extract_public_emails(_attr_text(anchor.get("href"))))
        emails.extend(found)
        if result.contact_page is None:
            result.contact_page = contact_url
    emails = list(dict.fromkeys(emails))
    result.email = _select_business_email(emails, final_url)
    result.website_url = final_url
    if result.email:
        result.evidence.append(("public_email", f"Public business email found on the first-party site: {result.email}", result.contact_page or final_url))
    if result.contact_page:
        result.evidence.append(("contact_page", "A first-party public contact/inquiry page was found.", result.contact_page))
    return result


def enrich_public_contact(business: DiscoveredBusiness) -> ContactResult:
    if business.provider == "demo":
        slug = normalize_business_name(business.name) or "demo"
        return ContactResult(
            email=f"hello@{slug}.invalid",
            website_url=business.website_url,
            contact_page=business.source_url,
            evidence=[("demo", "Synthetic public contact used for safe demo mode.", business.source_url or "https://example.invalid")],
        )

    result = ContactResult(website_url=business.website_url)
    if business.website_url and domain_of(business.website_url) not in SOCIAL_DOMAINS:
        result = contacts_from_website(business.website_url)
        if result.email:
            return result

    search = SearchClient()
    query = f'"{business.name}" "{business.city}" {business.state} email contact website'
    try:
        results = search.search(query)
    except Exception as exc:
        log.warning("Search enrichment failed for %s: %s", business.name, exc)
        return result

    snippet_emails: list[str] = []
    for item in results:
        # Do not take a journalist, directory employee or unrelated address from a broad snippet.
        # The result title must itself match the business, and a human still reviews the source.
        if _title_matches_business(item, business):
            snippet_emails.extend(extract_public_emails(item.get("snippet", "")))
        if not result.website_url and _likely_official(item, business):
            candidate_source = str(item.get("url") or "")
            result.website_url = normalize_url(candidate_source)
            result.evidence.append(("official_site_candidate", "Search results identified a likely first-party site; it must still be reviewed.", candidate_source))
    if result.website_url and result.website_url != business.website_url:
        site_result = contacts_from_website(result.website_url)
        result.email = site_result.email
        result.contact_page = site_result.contact_page
        result.website_url = site_result.website_url
        result.evidence.extend(site_result.evidence)
    if not result.email:
        result.email = _select_business_email(list(dict.fromkeys(snippet_emails)), result.website_url)
        if result.email:
            source = next(
                (
                    x.get("url")
                    for x in results
                    if _title_matches_business(x, business) and result.email in x.get("snippet", "")
                ),
                business.source_url or "",
            )
            result.evidence.append(("search_snippet_email", f"A public search-result snippet with a matching business title displayed {result.email}; verify it before approval.", str(source or "")))
    return result


def query_pairs(locations: list[str], industries: list[str]) -> list[tuple[str, str]]:
    """Rotate pairs by date so a nationwide campaign does not hit the same city daily."""
    pairs = list(itertools.product(locations, industries))
    if not pairs:
        return []
    offset = datetime.now(timezone.utc).date().toordinal() % len(pairs)
    return pairs[offset:] + pairs[:offset]
