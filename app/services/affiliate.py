from __future__ import annotations

import hashlib
from urllib.parse import urlparse

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database.saas_models import (
    AffiliateClick,
    AffiliateConversion,
    AnalyticsEvent,
    Business,
    LandingPage,
)
from ..models import Lead
from .compat_sync import sync_business_from_legacy


class AffiliateService:
    def landing_for_lead(self, session: Session, lead_id: int) -> tuple[Lead, Business, LandingPage] | None:
        lead = session.get(Lead, lead_id)
        if not lead or lead.data_mode != "live":
            return None
        business = sync_business_from_legacy(session, lead)
        landing = session.scalar(select(LandingPage).where(LandingPage.business_id == business.id))
        if not landing or not landing.published:
            return None
        return lead, business, landing

    @staticmethod
    def _privacy_hash(value: str) -> str:
        secret = get_settings().app_secret
        return hashlib.sha256(f"{secret}:{value}".encode()).hexdigest()

    def record_click(self, session: Session, request: Request, business: Business, landing: LandingPage) -> AffiliateClick:
        client_ip = request.client.host if request.client else "unknown"
        agent = request.headers.get("user-agent", "")[:500]
        referrer = request.headers.get("referer", "")
        try:
            referrer_domain = (urlparse(referrer).hostname or "")[:255]
        except ValueError:
            referrer_domain = ""
        click = AffiliateClick(
            business_id=business.id,
            campaign_id=business.campaign_id,
            landing_page_id=landing.id,
            ip_hash=self._privacy_hash(client_ip),
            user_agent_hash=self._privacy_hash(agent),
            referrer_domain=referrer_domain,
        )
        session.add(click)
        session.add(
            AnalyticsEvent(
                campaign_id=business.campaign_id,
                business_id=business.id,
                event_type="affiliate_click",
                properties={"landing_page_id": landing.id},
            )
        )
        return click

    def record_conversion(
        self,
        session: Session,
        *,
        business_id: int | None,
        campaign_id: int | None,
        external_reference: str | None,
        revenue,
        commission,
        currency: str = "USD",
    ) -> AffiliateConversion:
        conversion = AffiliateConversion(
            business_id=business_id,
            campaign_id=campaign_id,
            external_reference=external_reference,
            revenue=revenue,
            commission=commission,
            currency=currency,
        )
        session.add(conversion)
        session.add(
            AnalyticsEvent(
                campaign_id=campaign_id,
                business_id=business_id,
                event_type="affiliate_conversion",
                value=float(commission or 0),
                properties={"currency": currency},
            )
        )
        return conversion

    def summary(self, session: Session) -> dict:
        clicks = session.scalar(select(func.count(AffiliateClick.id))) or 0
        conversions = session.scalar(select(func.count(AffiliateConversion.id))) or 0
        revenue = session.scalar(select(func.coalesce(func.sum(AffiliateConversion.revenue), 0))) or 0
        commission = session.scalar(select(func.coalesce(func.sum(AffiliateConversion.commission), 0))) or 0
        return {
            "clicks": clicks,
            "conversions": conversions,
            "revenue": float(revenue),
            "commission": float(commission),
            "conversion_rate": round(conversions / clicks, 4) if clicks else 0,
        }
