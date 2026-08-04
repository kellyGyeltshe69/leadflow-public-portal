from __future__ import annotations

import csv
import io

from sqlalchemy.orm import Session

from ..models import Lead, utcnow
from ..utils import normalize_business_name, normalize_email, normalize_url
from .compat_sync import sync_business_from_legacy


class CSVImportService:
    REQUIRED_NAMES = {"business_name", "name"}

    def import_bytes(self, session: Session, campaign_id: int, payload: bytes, *, data_mode: str = "live") -> dict:
        if len(payload) > 5_000_000:
            raise ValueError("CSV is larger than 5 MB")
        text = payload.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or not self.REQUIRED_NAMES.intersection(reader.fieldnames):
            raise ValueError("CSV requires a business_name or name column")
        inserted = 0
        skipped = 0
        for index, row in enumerate(reader):
            if index >= 5000:
                raise ValueError("CSV row limit is 5,000")
            name = (row.get("business_name") or row.get("name") or "").strip()
            if not name:
                skipped += 1
                continue
            email = normalize_email(row.get("contact_email", "")) or None
            lead = Lead(
                campaign_id=campaign_id,
                business_name=name,
                normalized_name=normalize_business_name(name),
                industry=(row.get("industry") or row.get("category") or "Small business").strip(),
                city=(row.get("city") or "").strip(),
                state=(row.get("state") or "").strip(),
                formatted_address=(row.get("address") or "").strip(),
                website_url=normalize_url(row.get("website_url")),
                contact_email=email,
                contact_page=normalize_url(row.get("contact_page")),
                source_url=normalize_url(row.get("source_url")),
                discovery_provider="csv_import",
                status="needs_review" if email else "needs_contact",
                data_mode=data_mode,
                website_status="not_audited",
                last_verified_at=utcnow(),
            )
            session.add(lead)
            session.flush()
            sync_business_from_legacy(session, lead)
            inserted += 1
        return {"inserted": inserted, "skipped": skipped}
