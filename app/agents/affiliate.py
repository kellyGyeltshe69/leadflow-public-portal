from __future__ import annotations


class AffiliateRecommendationAgent:
    name = "affiliate_recommendation"

    def recommend(self, facts: dict) -> dict:
        ecommerce = bool(facts.get("ecommerce") or facts.get("online_ordering"))
        traffic = facts.get("traffic_estimate") or 0
        if ecommerce or traffic > 50_000:
            plan = "Hostinger Business or Cloud plan"
        else:
            plan = "Hostinger entry-level business website plan"
        return {
            "plan": plan,
            "rationale": "Recommendation is based on verified functionality needs, not private revenue.",
            "pricing_disclaimer": "Prices and renewal terms change; show the current Hostinger page rather than hardcoding a price.",
        }
