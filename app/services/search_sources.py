from __future__ import annotations

from dataclasses import dataclass

from ..discovery import SearchClient


@dataclass
class SourceResult:
    source: str
    title: str
    url: str
    snippet: str


class LicensedSearchSources:
    """Discovery hints through the configured licensed web-search API.

    LinkedIn, Facebook, Instagram, Yellow Pages, and directories are searched
    only as indexed result links. LeadFlow does not log in, bypass controls, or
    scrape those platforms. Official APIs/exports can be added per deployment.
    """

    SOURCE_QUERIES = {
        "google_search": "{name} {city} {state} official website",
        "linkedin_company": "site:linkedin.com/company {name} {city}",
        "facebook_business": "site:facebook.com {name} {city} business",
        "instagram_business": "site:instagram.com {name} {city}",
        "yellow_pages": "site:yellowpages.com {name} {city} {state}",
        "business_directories": "{name} {city} {state} chamber directory",
    }

    def search(self, name: str, city: str, state: str, limit_per_source: int = 3) -> list[SourceResult]:
        client = SearchClient()
        output = []
        for source, template in self.SOURCE_QUERIES.items():
            query = template.format(name=name, city=city, state=state)
            for result in client.search(query, limit=limit_per_source):
                output.append(
                    SourceResult(
                        source=source,
                        title=str(result.get("title") or ""),
                        url=str(result.get("url") or ""),
                        snippet=str(result.get("snippet") or ""),
                    )
                )
        return output
