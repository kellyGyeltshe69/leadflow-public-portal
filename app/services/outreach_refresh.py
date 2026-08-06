from __future__ import annotations

import threading

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..config import get_settings
from ..database.saas_models import EmailRecord
from ..db import session_scope
from ..models import Lead, Message
from ..outreach import compose_final_body
from .compat_sync import sync_email_from_legacy

_REFRESH_LOCK = threading.Lock()

TERMINAL_LEAD_STATUSES = {
    "replied",
    "opted_out",
    "bounced",
    "rejected",
    "do_not_contact",
    "converted",
}
TERMINAL_MESSAGE_STATUSES = {"sent", "cancelled"}


def refresh_unsent_outreach_links() -> dict[str, object]:
    """Recompose eligible unsent bodies from body_core and current settings.

    Sent/cancelled history is immutable. A non-blocking process lock prevents a
    startup refresh and a dashboard-triggered refresh from racing each other.
    """
    if not _REFRESH_LOCK.acquire(blocking=False):
        return {
            "status": "running",
            "reason": "Another hosted-link refresh is already running",
            "updated": 0,
        }
    try:
        return _refresh_unsent_outreach_links_locked()
    finally:
        _REFRESH_LOCK.release()


def _refresh_unsent_outreach_links_locked() -> dict[str, object]:
    settings = get_settings()
    if not settings.report_url_ready:
        return {
            "status": "skipped",
            "reason": "A valid public report origin is not configured",
            "report_base_url": settings.report_base_url,
            "updated": 0,
        }

    scanned = 0
    updated = 0
    localhost_replaced = 0
    canonical_updated = 0
    with session_scope() as session:
        messages = session.scalars(
            select(Message)
            .options(selectinload(Message.lead))
            .where(
                Message.sent_at.is_(None),
                Message.status.not_in(TERMINAL_MESSAGE_STATUSES),
                Message.lead.has(
                    (Lead.data_mode == "live")
                    & Lead.status.not_in(TERMINAL_LEAD_STATUSES)
                ),
            )
            .order_by(Message.lead_id, Message.stage)
        ).all()
        message_ids = [message.id for message in messages]
        canonical_by_legacy_id: dict[int, EmailRecord] = {}
        if message_ids:
            canonical = session.scalars(
                select(EmailRecord).where(EmailRecord.legacy_message_id.in_(message_ids))
            ).all()
            canonical_by_legacy_id = {
                item.legacy_message_id: item
                for item in canonical
                if item.legacy_message_id is not None
            }

        for message in messages:
            scanned += 1
            old_body = message.body_final or ""
            allow_placeholder = (
                message.status in {"draft", "failed"}
                or not settings.postal_ready
            )
            new_body = compose_final_body(
                message.lead,
                message,
                allow_postal_placeholder=allow_placeholder,
            )
            if new_body == old_body:
                continue
            if (
                "127.0.0.1" in old_body
                or "localhost" in old_body
                or "dashboard.render.com" in old_body
            ):
                localhost_replaced += 1
            message.body_final = new_body
            canonical_email = canonical_by_legacy_id.get(message.id)
            if canonical_email:
                canonical_email.body = new_body
                canonical_email.subject = message.subject
                canonical_email.status = message.status
                canonical_email.planned_at = message.planned_at
                canonical_email.sent_at = message.sent_at
                canonical_updated += 1
            else:
                sync_email_from_legacy(session, message.lead, message)
            updated += 1

    return {
        "status": "completed",
        "report_base_url": settings.report_base_url,
        "scanned": scanned,
        "updated": updated,
        "canonical_updated": canonical_updated,
        "localhost_replaced": localhost_replaced,
        "postal_ready": settings.postal_ready,
    }
