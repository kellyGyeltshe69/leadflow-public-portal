from __future__ import annotations


class CampaignPlannerAgent:
    name = "campaign_planner"

    def plan(self, timezone_name: str, business_category: str) -> dict:
        hour = 10 if any(term in business_category.lower() for term in ["restaurant", "cafe", "bakery"]) else 9
        return {
            "timezone": timezone_name,
            "best_send_time_local": f"{hour:02d}:15",
            "weekdays_only": True,
            "initial_test_size": 5,
            "rationale": "Use recipient-local business hours and expand only after relevance and bounce checks.",
        }
