from __future__ import annotations


class FollowUpAgent:
    name = "followup"

    def recommend(self, reply_probability: float, urgency: str = "low") -> dict:
        if reply_probability >= 0.35:
            days = [3, 8]
        elif reply_probability >= 0.15:
            days = [3, 7, 14]
        else:
            days = [4, 10]
        return {
            "necessary": True,
            "days": days,
            "tone": "helpful",
            "length": "short",
            "urgency": urgency,
            "stop_on": ["reply", "bounce", "unsubscribe", "conversion"],
        }
