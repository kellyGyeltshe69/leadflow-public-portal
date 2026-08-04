from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .composio_gateway import ComposioGateway
from .config import get_settings
from .db import session_scope
from .models import JobRun, Lead, Message, utcnow
from .runtime import get_runtime_mode

log = logging.getLogger(__name__)


def _iso(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else ""


def _unwrap(data: object) -> dict:
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected Google Sheets response through Composio: {type(data).__name__}")
    response_data = data.get("response_data")
    return response_data if isinstance(response_data, dict) else data


class GoogleSheetsReporter:
    def __init__(self) -> None:
        self.settings = get_settings()
        if not self.settings.sheets_configured:
            raise RuntimeError("COMPOSIO_API_KEY and GOOGLE_SHEET_ID are required for Sheets reporting")
        self.gateway = ComposioGateway()
        self.gateway.resolve_connection_id("googlesheets")
        self.spreadsheet_id = self.settings.google_sheet_id

    def _proxy(self, endpoint: str, method: str, body: object | None = None, parameters: list[dict] | None = None) -> dict:
        return _unwrap(self.gateway.proxy("googlesheets", endpoint, method, body=body, parameters=parameters))

    def ensure_tabs(self) -> None:
        info = self._proxy(
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}",
            "GET",
            parameters=[{"name": "fields", "value": "sheets.properties.title", "type": "query"}],
        )
        existing = {
            sheet.get("properties", {}).get("title", "")
            for sheet in info.get("sheets", [])
            if isinstance(sheet, dict)
        }
        wanted = [self.settings.google_sheet_leads_tab, self.settings.google_sheet_outreach_tab]
        requests = [{"addSheet": {"properties": {"title": title}}} for title in wanted if title not in existing]
        if requests:
            self._proxy(
                f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}:batchUpdate",
                "POST",
                body={"requests": requests},
            )

    def replace_values(self, tab: str, rows: list[list[object]]) -> None:
        range_name = f"'{tab}'!A1:Z"
        encoded = quote(range_name, safe="")
        self._proxy(
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values/{encoded}:clear",
            "POST",
            body={},
        )
        self._proxy(
            f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}/values/{encoded}",
            "PUT",
            body={"range": range_name, "majorDimension": "ROWS", "values": rows},
            parameters=[{"name": "valueInputOption", "value": "RAW", "type": "query"}],
        )

    def sync(self, leads: list[Lead], messages: list[Message]) -> dict:
        self.ensure_tabs()
        lead_rows: list[list[object]] = [[
            "Lead ID", "Business", "Industry", "City", "State", "Website", "Public Contact",
            "Website Status", "Verified Problems", "Need", "Ability", "Growth", "Response",
            "Weighted Score", "Lead Status", "Conversion Status", "Conversion Notes",
            "Created UTC", "Last Verified UTC", "Last Reply Sync UTC",
        ]]
        for lead in leads:
            lead_rows.append([
                lead.id,
                lead.business_name,
                lead.industry,
                lead.city,
                lead.state,
                lead.website_url or "",
                lead.contact_email or lead.contact_page or "",
                lead.website_status,
                " | ".join(lead.issues or []),
                lead.need_score,
                lead.ability_score,
                lead.growth_score,
                lead.response_score,
                lead.weighted_score,
                lead.status,
                lead.conversion_status,
                lead.conversion_notes,
                _iso(lead.created_at),
                _iso(lead.last_verified_at),
                _iso(lead.last_reply_sync_at),
            ])
        outreach_rows: list[list[object]] = [[
            "Message ID", "Lead ID", "Business", "Stage", "Day Offset", "Subject", "Body",
            "Status", "Planned UTC", "Sent UTC", "Gmail Thread ID", "Retry Count", "Error",
        ]]
        for message in messages:
            outreach_rows.append([
                message.id,
                message.lead_id,
                message.lead.business_name,
                message.stage,
                message.day_offset,
                message.subject,
                message.body_final,
                message.status,
                _iso(message.planned_at),
                _iso(message.sent_at),
                message.gmail_thread_id or "",
                message.retry_count,
                message.error or "",
            ])
        self.replace_values(self.settings.google_sheet_leads_tab, lead_rows)
        self.replace_values(self.settings.google_sheet_outreach_tab, outreach_rows)
        return {"leads": len(leads), "messages": len(messages), "spreadsheet_id": self.spreadsheet_id}


def sync_google_sheet_job() -> dict:
    settings = get_settings()
    runtime_mode = get_runtime_mode()
    if runtime_mode == "demo":
        return {"status": "skipped", "reason": "demo mode"}
    if not settings.sheets_configured:
        return {"status": "skipped", "reason": "Google Sheet is not configured"}
    with session_scope() as session:
        job = JobRun(job_type="sync_sheet", status="running")
        session.add(job)
        session.flush()
        job_id = job.id
    try:
        with session_scope() as session:
            leads = session.scalars(
                select(Lead).where(Lead.data_mode == "live").order_by(Lead.weighted_score.desc(), Lead.id)
            ).all()
            messages = session.scalars(
                select(Message)
                .options(selectinload(Message.lead))
                .where(Message.lead.has(Lead.data_mode == "live"))
                .order_by(Message.lead_id, Message.stage)
            ).all()
            result = GoogleSheetsReporter().sync(leads, messages)
        result = {"status": "completed", **result}
        with session_scope() as session:
            job = session.get(JobRun, job_id)
            job.status = "completed"
            job.summary = str(result)
            job.finished_at = utcnow()
        return result
    except Exception as exc:
        log.exception("Google Sheets sync failed")
        with session_scope() as session:
            job = session.get(JobRun, job_id)
            if job:
                job.status = "failed"
                job.error = str(exc)
                job.finished_at = utcnow()
        raise
