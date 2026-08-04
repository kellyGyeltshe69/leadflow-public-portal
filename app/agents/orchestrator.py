from __future__ import annotations

from .affiliate import AffiliateRecommendationAgent
from .analytics import AnalyticsAgent
from .campaign_planner import CampaignPlannerAgent
from .conversion import ConversionOptimizerAgent
from .email_copywriter import EmailCopywriterAgent
from .followup import FollowUpAgent
from .lead_discovery import LeadDiscoveryAgent
from .schemas import LeadIntelligence
from .seo import SEOAgent


class LeadIntelligenceOrchestrator:
    """Coordinates narrow agents without granting them external side effects."""

    def analyze(self, business: dict, base_subject: str, base_body: str) -> LeadIntelligence:
        facts = business.get("audit_facts") or {}
        issues = list(business.get("issues") or [])
        lead_score = float(business.get("weighted_score", 0))
        discovery = LeadDiscoveryAgent().prioritize(business)
        seo = SEOAgent().score(facts)
        affiliate = AffiliateRecommendationAgent().recommend(facts)
        copywriter = EmailCopywriterAgent()
        subjects = copywriter.subjects(
            business.get("business_name", "the business"),
            issues[0] if issues else "website opportunity",
        )
        variants = copywriter.variants(
            business.get("business_name", "the business"),
            base_subject,
            base_body,
            lead_score,
        )
        best_quality = max((variant.quality_score for variant in variants), default=60)
        predictions = AnalyticsAgent().predict(
            lead_score=lead_score,
            email_quality=best_quality,
            has_public_email=bool(business.get("contact_email")),
        )
        planner = CampaignPlannerAgent().plan(
            business.get("timezone_name", "America/New_York"),
            business.get("industry", "small business"),
        )
        followup = FollowUpAgent().recommend(predictions["predicted_reply_rate"])
        solutions = [
            business.get("opportunity") or "Fix the verified issue before considering a platform migration.",
            "Create one mobile-first customer action and measure completed inquiries.",
            "Keep the business details consistent across the website and Google profile.",
        ]
        conversion = ConversionOptimizerAgent().landing_content(business, issues, affiliate)
        return LeadIntelligence(
            summary=f"Priority {discovery['priority_score']}/10; SEO evidence score {seo['score']}/100.",
            problems=issues,
            personalized_solutions=solutions,
            recommended_hostinger_plan=affiliate["plan"],
            plan_rationale=affiliate["rationale"],
            reply_probability=predictions["predicted_reply_rate"],
            conversion_probability=predictions["predicted_conversion_rate"],
            best_send_time_local=planner["best_send_time_local"],
            followup_strategy=followup,
            cta=conversion["cta"],
            subject_lines=subjects,
            email_variants=variants,
            limitations=[
                predictions["disclaimer"],
                seo["limitations"],
                affiliate["pricing_disclaimer"],
                "Predictions are ranking aids, not observed outcomes or guarantees.",
            ],
        )
