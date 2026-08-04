from __future__ import annotations

import re

from .analytics import AnalyticsAgent
from .schemas import EmailVariantData, SubjectCandidate

STYLES = ["professional", "friendly", "agency", "consultant", "short", "long", "cold_outreach", "follow_up", "last_reminder"]
SPAM_TERMS = {"guaranteed", "act now", "limited time", "urgent", "free money"}


class EmailCopywriterAgent:
    name = "email_copywriter"

    def quality_score(self, business_name: str, body: str) -> float:
        score = 70.0
        words = body.split()
        if business_name.lower() in body.lower():
            score += 10
        if 45 <= len(words) <= 180:
            score += 10
        if "?" in body:
            score += 5
        if any(term in body.lower() for term in SPAM_TERMS):
            score -= 25
        if len(re.findall(r"https?://", body)) > 1:
            score -= 10
        return max(0, min(100, round(score, 1)))

    def subjects(self, business_name: str, primary_problem: str) -> list[SubjectCandidate]:
        problem = primary_problem.rstrip(".")[:70]
        candidates = [
            f"One website suggestion for {business_name}",
            f"A quick public-site observation for {business_name}",
            f"Website idea for {business_name}",
            f"A practical fix for {business_name}",
            f"Question about {business_name}'s website",
            f"A mobile website suggestion for {business_name}",
            f"Improving the online path for {business_name}",
            f"One customer-journey idea for {business_name}",
            f"A short website checklist for {business_name}",
            f"Public website note: {problem}",
        ]
        return [SubjectCandidate(text=value, score=max(55, 92 - index * 3), rationale="Specific, truthful, and non-urgent.") for index, value in enumerate(candidates)]

    def variants(self, business_name: str, base_subject: str, base_body: str, lead_score: float) -> list[EmailVariantData]:
        variants = []
        for style in STYLES:
            body = base_body
            if style == "friendly":
                body = body.replace("I reviewed", "I took a quick look at")
            elif style == "agency":
                body += "\n\nA simple first version can focus on one measurable customer action."
            elif style == "consultant":
                body += "\n\nMy suggestion is to correct the verified issue before considering a rebuild."
            elif style == "short":
                body = " ".join(body.split()[:90])
            elif style == "long":
                body += "\n\nIf helpful, I can send a five-point implementation checklist with no obligation."
            elif style == "follow_up":
                body = f"Hi {business_name} team,\n\nOne practical follow-up: the public website observation can usually be addressed with a focused first step rather than a full rebuild. Would the short checklist help?"
            elif style == "last_reminder":
                body = f"Hi {business_name} team,\n\nI am closing the loop. The website observation may still be useful, but I will not follow up again."
            quality = self.quality_score(business_name, body)
            predictions = AnalyticsAgent().predict(lead_score=lead_score, email_quality=quality, has_public_email=True)
            variants.append(
                EmailVariantData(
                    style=style,
                    subject=base_subject,
                    body=body,
                    quality_score=quality,
                    predicted_open_rate=predictions["predicted_open_rate"],
                    predicted_click_rate=predictions["predicted_click_rate"],
                    predicted_reply_rate=predictions["predicted_reply_rate"],
                )
            )
        return variants
