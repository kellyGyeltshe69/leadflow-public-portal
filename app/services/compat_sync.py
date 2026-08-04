from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.saas_models import (
    AuditHistory,
    Business,
    EmailRecord,
    FollowUp,
    LandingPage,
)
from ..models import Lead, Message


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:120] or "business"


def sync_business_from_legacy(session: Session, lead: Lead) -> Business:
    business = session.scalar(select(Business).where(Business.legacy_lead_id == lead.id))
    if not business:
        business = Business(
            campaign_id=lead.campaign_id,
            legacy_lead_id=lead.id,
            name=lead.business_name,
        )
        session.add(business)
        session.flush()
    business.external_place_id = lead.place_id
    business.name = lead.business_name
    business.industry = lead.industry
    business.category = lead.industry
    business.city = lead.city
    business.state = lead.state
    business.address = lead.formatted_address
    business.website_url = lead.website_url
    business.contact_email = lead.contact_email
    business.contact_page = lead.contact_page
    business.source_urls = [x for x in [lead.source_url, lead.maps_url] if x]
    business.audit_summary = {
        "website_status": lead.website_status,
        "facts": lead.audit_facts or {},
        "issues": lead.issues or [],
        "opportunity": lead.opportunity,
        "checklist": lead.checklist or [],
    }
    business.website_score = max(0, min(100, lead.need_score * 10))
    business.weighted_score = lead.weighted_score
    business.reply_probability = max(0, min(1, lead.response_score / 10))
    business.conversion_probability = max(0, min(1, lead.weighted_score / 14))
    business.google_rating = (lead.audit_facts or {}).get("google_rating")
    business.google_review_count = (lead.audit_facts or {}).get("google_review_count")
    business.status = lead.status
    business.data_mode = lead.data_mode
    business.last_verified_at = lead.last_verified_at

    latest = session.scalar(
        select(AuditHistory)
        .where(AuditHistory.business_id == business.id)
        .order_by(AuditHistory.created_at.desc())
        .limit(1)
    )
    if not latest or latest.facts != (lead.audit_facts or {}) or latest.issues != (lead.issues or []):
        session.add(
            AuditHistory(
                business_id=business.id,
                score=business.website_score,
                facts=lead.audit_facts or {},
                issues=lead.issues or [],
                screenshot_path=(lead.audit_facts or {}).get("screenshot_path"),
            )
        )

    landing = session.scalar(select(LandingPage).where(LandingPage.business_id == business.id))
    if not landing:
        landing = LandingPage(
            business_id=business.id,
            slug=f"{_slug(lead.business_name)}-{business.id}",
            headline=f"Website opportunity report for {lead.business_name}",
            content=business.audit_summary,
            published=lead.data_mode == "live",
        )
        session.add(landing)
    else:
        landing.content = business.audit_summary
        landing.published = lead.data_mode == "live"
    return business


def sync_email_from_legacy(session: Session, lead: Lead, message: Message) -> EmailRecord:
    business = sync_business_from_legacy(session, lead)
    email = session.scalar(select(EmailRecord).where(EmailRecord.legacy_message_id == message.id))
    if not email:
        email = EmailRecord(
            business_id=business.id,
            campaign_id=lead.campaign_id,
            legacy_message_id=message.id,
            stage=message.stage,
            subject=message.subject,
            body=message.body_final or message.body_core,
        )
        session.add(email)
        session.flush()
    email.subject = message.subject
    email.body = message.body_final or message.body_core
    email.status = message.status
    email.planned_at = message.planned_at
    email.sent_at = message.sent_at
    email.provider_message_id = message.gmail_message_id
    email.thread_id = message.gmail_thread_id
    email.quality_score = max(0, min(100, lead.weighted_score * 10))
    email.estimated_reply_rate = max(0, min(1, lead.response_score / 10))

    if message.stage > 0:
        followup = session.scalar(
            select(FollowUp).where(
                FollowUp.business_id == business.id,
                FollowUp.sequence_number == message.stage,
            )
        )
        if not followup:
            followup = FollowUp(
                business_id=business.id,
                email_id=email.id,
                sequence_number=message.stage,
            )
            session.add(followup)
        followup.scheduled_at = message.planned_at
        followup.status = message.status
    return email
