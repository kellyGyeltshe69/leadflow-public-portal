from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...database.saas_models import User
from ...models import Campaign, utcnow
from ...repositories.campaigns import CampaignRepository, slugify
from ...workers.queue import dispatch_job
from ..deps import get_db, require_role

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


class CampaignInput(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    locations: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    daily_limit: int = Field(default=10, ge=1, le=50)
    minimum_score: float = Field(default=7, ge=1, le=10)
    schedule: dict = Field(default_factory=dict)
    template: dict = Field(default_factory=dict)


class CampaignView(CampaignInput):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str | None
    status: str
    active: bool


@router.get("", response_model=list[CampaignView])
def list_campaigns(session: Session = Depends(get_db), _user: User = Depends(require_role("viewer"))):
    return list(session.scalars(select(Campaign).order_by(Campaign.created_at.desc())).all())


@router.post("", response_model=CampaignView)
def create_campaign(data: CampaignInput, session: Session = Depends(get_db), user: User = Depends(require_role("marketer"))):
    campaign = Campaign(
        name=data.name,
        slug=f"{slugify(data.name)}-{user.id}-{int(utcnow().timestamp())}",
        owner_id=user.id,
        locations=data.locations,
        industries=data.industries,
        daily_limit=data.daily_limit,
        minimum_score=data.minimum_score,
        schedule=data.schedule,
        template=data.template,
        status="paused",
        active=False,
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    return campaign


@router.post("/{campaign_id}/pause", response_model=CampaignView)
def pause(campaign_id: int, session: Session = Depends(get_db), _user: User = Depends(require_role("marketer"))):
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    campaign.status, campaign.active = "paused", False
    session.commit()
    return campaign


@router.post("/{campaign_id}/resume", response_model=CampaignView)
def resume(campaign_id: int, session: Session = Depends(get_db), _user: User = Depends(require_role("marketer"))):
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    campaign.status, campaign.active = "active", True
    session.commit()
    return campaign


@router.post("/{campaign_id}/clone", response_model=CampaignView)
def clone(campaign_id: int, session: Session = Depends(get_db), user: User = Depends(require_role("marketer"))):
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    cloned = CampaignRepository(session).clone(campaign, owner_id=user.id)
    session.commit()
    return cloned


@router.post("/{campaign_id}/discover", status_code=202)
def discover(campaign_id: int, session: Session = Depends(get_db), _user: User = Depends(require_role("marketer"))):
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    return dispatch_job(
        "discover",
        False,
        campaign_id=campaign_id,
        job_type=f"discover_campaign_{campaign_id}",
    )


@router.post("/{campaign_id}/archive", status_code=204)
def archive(campaign_id: int, session: Session = Depends(get_db), _user: User = Depends(require_role("admin"))):
    campaign = session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    CampaignRepository(session).archive(campaign)
    session.commit()
