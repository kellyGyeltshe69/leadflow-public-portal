from __future__ import annotations


class LeadDiscoveryAgent:
    name = "lead_discovery"

    def prioritize(self, business: dict) -> dict:
        score = float(business.get("weighted_score", 0))
        contact_bonus = 1 if business.get("contact_email") else 0
        return {
            "priority_score": min(10, round(score + contact_bonus, 1)),
            "reason": "Verified need and a public business contact are weighted above unverified reach estimates.",
        }
