from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...database.saas_models import Business, EmailRecord, Reply, User
from ...services.affiliate import AffiliateService
from ..deps import get_db, require_role

router = APIRouter(prefix="/analytics", tags=["analytics"])


class ConversionInput(BaseModel):
    business_id: int | None = None
    campaign_id: int | None = None
    external_reference: str | None = None
    revenue: Decimal = Decimal("0")
    commission: Decimal = Decimal("0")
    currency: str = "USD"


@router.get("/summary")
def summary(session: Session = Depends(get_db), _user: User = Depends(require_role("viewer"))):
    total = session.scalar(select(func.count(Business.id)).where(Business.data_mode == "live")) or 0
    qualified = session.scalar(
        select(func.count(Business.id)).where(Business.data_mode == "live", Business.weighted_score >= 7)
    ) or 0
    sent = session.scalar(select(func.count(EmailRecord.id)).where(EmailRecord.status == "sent")) or 0
    replies = session.scalar(select(func.count(Reply.id)).where(Reply.classification == "reply")) or 0
    affiliate = AffiliateService().summary(session)
    return {
        "total_leads": total,
        "qualified_leads": qualified,
        "emails_sent": sent,
        "replies": replies,
        "reply_rate": round(replies / sent, 4) if sent else 0,
        "open_rate": None,
        "open_rate_note": "Disabled: LeadFlow does not use invisible email tracking pixels.",
        **affiliate,
    }


@router.post("/conversions", status_code=201)
def record_conversion(
    data: ConversionInput,
    session: Session = Depends(get_db),
    _user: User = Depends(require_role("marketer")),
):
    conversion = AffiliateService().record_conversion(
        session,
        business_id=data.business_id,
        campaign_id=data.campaign_id,
        external_reference=data.external_reference,
        revenue=data.revenue,
        commission=data.commission,
        currency=data.currency.upper()[:3],
    )
    session.commit()
    return {"id": conversion.id, "status": conversion.status}
