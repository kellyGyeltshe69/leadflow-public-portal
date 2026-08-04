from __future__ import annotations


class AnalyticsAgent:
    name = "analytics"

    def predict(self, *, lead_score: float, email_quality: float, has_public_email: bool) -> dict:
        # Heuristic estimates are intentionally labeled predictions, not measured rates.
        reply = min(0.45, max(0.01, lead_score / 35 + email_quality / 500 - (0 if has_public_email else 0.2)))
        conversion = min(0.25, max(0.005, reply * (lead_score / 12)))
        click = min(0.5, max(0.01, reply * 1.15))
        open_estimate = min(0.75, max(0.05, 0.25 + email_quality / 250))
        return {
            "predicted_open_rate": round(open_estimate, 3),
            "predicted_click_rate": round(click, 3),
            "predicted_reply_rate": round(reply, 3),
            "predicted_conversion_rate": round(conversion, 3),
            "disclaimer": "Heuristic estimate; not an observed or guaranteed result.",
        }
