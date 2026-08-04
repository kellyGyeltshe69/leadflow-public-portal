from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Campaign, utcnow
from .base import Repository


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:160] or "campaign"


class CampaignRepository(Repository[Campaign]):
    def __init__(self, session: Session):
        super().__init__(session, Campaign)

    def active(self) -> list[Campaign]:
        return list(
            self.session.scalars(
                select(Campaign).where(Campaign.status == "active", Campaign.active.is_(True)).order_by(Campaign.created_at)
            ).all()
        )

    def clone(self, campaign: Campaign, *, owner_id: int | None = None) -> Campaign:
        clone = Campaign(
            name=f"{campaign.name} Copy",
            slug=f"{slugify(campaign.name)}-copy-{int(utcnow().timestamp())}",
            owner_id=owner_id or campaign.owner_id,
            status="paused",
            active=False,
            template=dict(campaign.template or {}),
            schedule=dict(campaign.schedule or {}),
            locations=list(campaign.locations or []),
            industries=list(campaign.industries or []),
            daily_limit=campaign.daily_limit,
            minimum_score=campaign.minimum_score,
            discovery_hour_utc=campaign.discovery_hour_utc,
            cloned_from_id=campaign.id,
        )
        return self.add(clone)

    def archive(self, campaign: Campaign) -> None:
        campaign.status = "archived"
        campaign.active = False
        campaign.archived_at = utcnow()
