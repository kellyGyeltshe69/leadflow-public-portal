from __future__ import annotations


class SEOAgent:
    name = "seo"

    def score(self, facts: dict) -> dict:
        checks = {
            "title": bool(facts.get("title")),
            "meta_description": bool(facts.get("meta_description_present")),
            "sitemap": bool(facts.get("sitemap_present")),
            "robots": facts.get("robots_txt_status") in {200, 404},
            "mobile_viewport": bool(facts.get("viewport_present")),
        }
        score = round(sum(checks.values()) / len(checks) * 100, 1)
        return {"score": score, "checks": checks, "limitations": "Domain authority and traffic require a licensed external dataset."}
