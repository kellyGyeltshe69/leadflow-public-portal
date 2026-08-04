from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.saas_models import Business
from .base import Repository


class BusinessRepository(Repository[Business]):
    def __init__(self, session: Session):
        super().__init__(session, Business)

    def for_campaign(
        self,
        campaign_id: int,
        *,
        status: str | None = None,
        data_mode: str = "live",
        offset: int = 0,
        limit: int = 100,
    ) -> list[Business]:
        statement = select(Business).where(
            Business.campaign_id == campaign_id,
            Business.data_mode == data_mode,
        )
        if status:
            statement = statement.where(Business.status == status)
        return list(
            self.session.scalars(
                statement.order_by(Business.weighted_score.desc()).offset(offset).limit(max(1, min(limit, 500)))
            ).all()
        )

    def by_legacy_lead(self, lead_id: int) -> Business | None:
        return self.session.scalar(select(Business).where(Business.legacy_lead_id == lead_id))
