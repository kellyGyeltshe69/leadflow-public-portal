from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...database.saas_models import Business, EmailRecord, EmailVariant, User
from ...models import Campaign, Lead, Message
from ...outreach import compose_final_body
from ...services.csv_import import CSVImportService
from ..deps import get_db, require_role

router = APIRouter(prefix="/businesses", tags=["businesses"])


class VariantSelection(BaseModel):
    style: str


class BusinessView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    name: str
    industry: str
    city: str
    state: str
    website_url: str | None
    contact_email: str | None
    status: str
    data_mode: str
    weighted_score: float
    reply_probability: float
    conversion_probability: float
    audit_summary: dict


@router.get("", response_model=list[BusinessView])
def list_businesses(
    campaign_id: int | None = None,
    status: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_db),
    _user: User = Depends(require_role("viewer")),
):
    statement = select(Business).where(Business.data_mode == "live")
    if campaign_id is not None:
        statement = statement.where(Business.campaign_id == campaign_id)
    if status:
        statement = statement.where(Business.status == status)
    return list(session.scalars(statement.order_by(Business.weighted_score.desc()).offset(offset).limit(limit)).all())


@router.post("/import-csv", status_code=201)
async def import_csv(
    campaign_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_db),
    _user: User = Depends(require_role("marketer")),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Upload a .csv file")
    if not session.get(Campaign, campaign_id):
        raise HTTPException(404, "Campaign not found")
    try:
        result = CSVImportService().import_bytes(session, campaign_id, await file.read(), data_mode="live")
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    session.commit()
    return result


@router.get("/{business_id}/email-variants")
def email_variants(
    business_id: int,
    session: Session = Depends(get_db),
    _user: User = Depends(require_role("viewer")),
):
    email = session.scalar(
        select(EmailRecord).where(EmailRecord.business_id == business_id, EmailRecord.stage == 0)
    )
    if not email:
        raise HTTPException(404, "Initial email not found")
    return [
        {
            "style": item.style,
            "subject": item.subject,
            "body": item.body,
            "quality_score": item.quality_score,
            "predicted_open_rate": item.predicted_open_rate,
            "predicted_click_rate": item.predicted_click_rate,
            "predicted_reply_rate": item.predicted_reply_rate,
            "selected": item.selected,
        }
        for item in session.scalars(
            select(EmailVariant).where(EmailVariant.email_id == email.id).order_by(EmailVariant.quality_score.desc())
        ).all()
    ]


@router.post("/{business_id}/email-variants/select")
def select_email_variant(
    business_id: int,
    data: VariantSelection,
    session: Session = Depends(get_db),
    _user: User = Depends(require_role("marketer")),
):
    business = session.get(Business, business_id)
    if not business:
        raise HTTPException(404, "Business not found")
    email = session.scalar(
        select(EmailRecord).where(EmailRecord.business_id == business_id, EmailRecord.stage == 0)
    )
    if not email:
        raise HTTPException(404, "Initial email not found")
    variants = session.scalars(select(EmailVariant).where(EmailVariant.email_id == email.id)).all()
    selected = next((item for item in variants if item.style == data.style), None)
    if not selected:
        raise HTTPException(404, "Variant not found")
    for item in variants:
        item.selected = item.id == selected.id
    email.style = selected.style
    email.subject = selected.subject
    email.body = selected.body
    email.quality_score = selected.quality_score
    if email.legacy_message_id and business.legacy_lead_id:
        legacy_email = session.get(Message, email.legacy_message_id)
        lead = session.get(Lead, business.legacy_lead_id)
        if legacy_email and lead and legacy_email.status in {"draft", "scheduled", "retry"}:
            legacy_email.subject = selected.subject
            legacy_email.body_core = selected.body
            legacy_email.body_final = compose_final_body(
                lead,
                legacy_email,
                allow_postal_placeholder=legacy_email.status == "draft",
            )
    session.commit()
    return {"selected": selected.style, "quality_score": selected.quality_score}


@router.get("/{business_id}", response_model=BusinessView)
def business_detail(business_id: int, session: Session = Depends(get_db), _user: User = Depends(require_role("viewer"))):
    business = session.get(Business, business_id)
    if not business:
        raise HTTPException(404, "Business not found")
    return business
