from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents import LeadIntelligenceOrchestrator
from ..database.saas_models import EmailRecord, EmailVariant, LandingPage
from ..models import Lead
from .compat_sync import sync_business_from_legacy


def apply_intelligence(session: Session, lead: Lead) -> dict:
    if not lead.messages:
        return {}
    first = next((message for message in lead.messages if message.stage == 0), lead.messages[0])
    payload = {
        "business_name": lead.business_name,
        "industry": lead.industry,
        "timezone_name": lead.timezone_name,
        "contact_email": lead.contact_email,
        "weighted_score": lead.weighted_score,
        "audit_facts": lead.audit_facts or {},
        "issues": lead.issues or [],
        "opportunity": lead.opportunity,
    }
    intelligence = LeadIntelligenceOrchestrator().analyze(payload, first.subject, first.body_core)
    data = intelligence.model_dump()
    lead.audit_facts = {**(lead.audit_facts or {}), "ai_intelligence": data}
    business = sync_business_from_legacy(session, lead)
    business.reply_probability = intelligence.reply_probability
    business.conversion_probability = intelligence.conversion_probability
    business.audit_summary = {**(business.audit_summary or {}), "ai_intelligence": data}

    canonical_email = session.scalar(
        select(EmailRecord).where(EmailRecord.legacy_message_id == first.id)
    )
    if canonical_email:
        if intelligence.email_variants:
            best = max(intelligence.email_variants, key=lambda item: item.quality_score)
            canonical_email.quality_score = best.quality_score
            canonical_email.estimated_open_rate = best.predicted_open_rate
            canonical_email.estimated_click_rate = best.predicted_click_rate
            canonical_email.estimated_reply_rate = best.predicted_reply_rate
        for variant in intelligence.email_variants:
            record = session.scalar(
                select(EmailVariant).where(
                    EmailVariant.email_id == canonical_email.id,
                    EmailVariant.style == variant.style,
                )
            )
            if not record:
                record = EmailVariant(email_id=canonical_email.id, style=variant.style, subject=variant.subject, body=variant.body)
                session.add(record)
            record.subject = variant.subject
            record.body = variant.body
            record.quality_score = variant.quality_score
            record.predicted_open_rate = variant.predicted_open_rate
            record.predicted_click_rate = variant.predicted_click_rate
            record.predicted_reply_rate = variant.predicted_reply_rate

    landing = session.scalar(select(LandingPage).where(LandingPage.business_id == business.id))
    if landing:
        landing.content = {
            **(landing.content or {}),
            "ai_intelligence": data,
            "subject_lines": [subject.model_dump() for subject in intelligence.subject_lines],
        }
        landing.recommended_plan = intelligence.recommended_hostinger_plan
    return data
