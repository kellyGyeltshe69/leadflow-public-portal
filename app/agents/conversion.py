from __future__ import annotations


class ConversionOptimizerAgent:
    name = "conversion_optimizer"

    def landing_content(self, business: dict, problems: list[str], plan: dict) -> dict:
        return {
            "headline": f"A practical website improvement plan for {business.get('business_name', 'your business')}",
            "problems": problems[:6],
            "benefits": [
                "An owned destination independent of social platforms",
                "A clear mobile inquiry or booking path",
                "Reliable hosting, HTTPS, and backups",
            ],
            "recommendation": plan,
            "faq": [
                {"question": "Is a platform change required?", "answer": "No. Fix verified issues first; migrate only when it adds value."},
                {"question": "Is the recommendation independent?", "answer": "The sender is an independent Hostinger affiliate and may earn a commission."},
            ],
            "cta": "Review the current hosting options",
            "testimonials": [],
            "testimonial_note": "No testimonials are displayed unless real, approved testimonials are configured.",
        }
