from __future__ import annotations

import csv
import io
import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select, text
from sqlalchemy.orm import selectinload
from starlette.middleware.sessions import SessionMiddleware

from .ai import ai_backend_report
from .api.v1.router import router as api_v1_router
from .composio_gateway import TOOLKITS, ComposioGateway
from .config import get_settings
from .core.auth import bootstrap_admin_user
from .core.logging import configure_logging
from .core.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from .database.saas_models import EmailRecord, LogRecord, TaskRecord, User
from .db import init_db, session_scope
from .gmail import cancel_future_messages
from .models import Campaign, DoNotContact, JobRun, Lead, Message, utcnow
from .outreach import compose_final_body
from .pipeline import (
    approve_lead,
    discover_job,
    ensure_default_campaign,
    fast_start_job,
    reject_lead,
    reverify_lead,
    sync_replies_job,
    unsubscribe_lead,
)
from .runtime import ensure_system_state, get_runtime_mode, set_runtime_mode
from .scheduler import start_scheduler, stop_scheduler
from .security import (
    csrf_token,
    landing_token,
    require_admin,
    unsubscribe_token,
    verify_csrf,
    verify_landing_token,
    verify_unsubscribe_token,
)
from .services.affiliate import AffiliateService
from .services.audit_log import record_log
from .services.compat_sync import sync_business_from_legacy, sync_email_from_legacy
from .workers.queue import dispatch_job

configure_logging(json_logs=True)
log = logging.getLogger(__name__)
settings = get_settings()
templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not settings.demo_mode and not settings.admin_security_ready:
        raise RuntimeError("Production data mode requires a 32+ character APP_SECRET and 16+ character ADMIN_PASSWORD")
    if not settings.demo_mode and not settings.composio_ready:
        raise RuntimeError("Production data mode requires COMPOSIO_API_KEY and COMPOSIO_USER_ID")
    init_db()
    bootstrap_admin_user()
    ensure_system_state()
    ensure_default_campaign()
    if not settings.async_workers_enabled:
        # Local background tasks cannot survive a web-process restart. Mark any
        # leftover rows explicitly instead of showing them as running forever.
        with session_scope() as session:
            interrupted = session.scalars(
                select(JobRun).where(JobRun.status == "running")
            ).all()
            for job in interrupted:
                job.status = "failed"
                job.error = "Interrupted by the previous local LeadFlow process shutdown."
                job.finished_at = utcnow()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title=settings.app_name, version="1.3.1", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.app_secret,
    same_site="lax",
    https_only=settings.public_url_ready,
    max_age=600,
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(api_v1_router)
admin = APIRouter(dependencies=[Depends(require_admin)])


def context(request: Request, **extra):
    return {
        "request": request,
        "settings": settings,
        "runtime_mode": get_runtime_mode(),
        "csrf": csrf_token(),
        **extra,
    }


def check_csrf(value: str) -> None:
    if not verify_csrf(value):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def redirect(path: str, message: str = "", error: str = "") -> RedirectResponse:
    params = []
    if message:
        params.append("message=" + quote(message))
    if error:
        params.append("error=" + quote(error))
    return RedirectResponse(path + (("?" + "&".join(params)) if params else ""), status_code=303)


def _private_page_headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _parse_job_summary(summary: str) -> dict:
    try:
        value = json.loads(summary) if summary else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _safe_job_error(value: str) -> str:
    text_value = str(value or "")[:800]
    text_value = re.sub(r"\b(?:ac|ca)_[A-Za-z0-9_-]+\b", "[redacted connection id]", text_value)
    return re.sub(
        r"(?i)(api[_ -]?key\s*[=:]\s*)\S+",
        r"\1[redacted]",
        text_value,
    )


def _latest_discovery_job(session):
    running = session.scalar(
        select(JobRun)
        .where(JobRun.job_type.like("%discover%"), JobRun.status == "running")
        .order_by(JobRun.started_at.desc())
        .limit(1)
    )
    if running:
        return running
    return session.scalar(
        select(JobRun)
        .where(JobRun.job_type.like("%discover%"))
        .order_by(JobRun.started_at.desc())
        .limit(1)
    )


def _discovery_job_payload(job: JobRun | None) -> dict | None:
    if not job:
        return None
    progress = _parse_job_summary(job.summary)
    allowed = {
        "phase", "message", "updated_at", "provider", "data_mode", "current_query",
        "current_business", "current_website", "current_lead_status", "current_score",
        "ai_provider", "query_candidates", "queries_attempted", "candidates_seen",
        "inserted", "duplicates_skipped", "websites_audited", "public_contacts_found",
        "pending_approval", "candidate_target", "qualified_target", "events",
    }
    safe_progress = {key: progress.get(key) for key in allowed if key in progress}
    events = safe_progress.get("events")
    if not isinstance(events, list):
        safe_progress["events"] = []
    else:
        safe_progress["events"] = [event for event in events[-12:] if isinstance(event, dict)]
    safe_progress["phase"] = safe_progress.get("phase") or job.status
    safe_progress["message"] = safe_progress.get("message") or f"Discovery job is {job.status}."
    safe_progress["status"] = job.status
    finished_at = job.finished_at or utcnow()
    elapsed_seconds = max(0, int((finished_at - job.started_at).total_seconds()))
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "started_at": job.started_at.isoformat() + "Z",
        "finished_at": job.finished_at.isoformat() + "Z" if job.finished_at else None,
        "elapsed_seconds": elapsed_seconds,
        "error": _safe_job_error(job.error),
        "progress": safe_progress,
    }


@app.get("/health")
def health():
    runtime_mode = get_runtime_mode()
    demo_mode = runtime_mode == "demo"
    return {
        "status": "ok",
        "runtime_mode": runtime_mode,
        "demo_mode": demo_mode,
        "sending_enabled": settings.sending_enabled,
        "ai_provider": settings.ai_provider,
        "fast_start_enabled": settings.fast_start_enabled,
        "startup_discovery_target": settings.startup_discovery_target,
        "configured_total_send_limit": settings.daily_total_send_limit,
        "effective_total_send_limit": settings.effective_total_send_limit,
        "production_send_ready": settings.production_send_ready_for(demo_mode),
        "gates": {
            "postal_address": settings.postal_ready,
            "composio_configured": settings.composio_ready,
            "gmail_via_composio_configured": settings.gmail_ready,
            "google_maps_via_composio_configured": settings.composio_ready and not demo_mode,
            "google_sheets_report_configured": settings.sheets_configured,
            "ai_backend_configured": settings.ai_ready_for(demo_mode),
            "https_public_report_url": settings.report_url_ready,
            "https_dashboard_url": settings.public_url_ready,
            "admin_security": settings.admin_security_ready,
            "high_volume_acknowledged": settings.high_volume_acknowledged,
            "mailbox_authentication_confirmed": settings.mailbox_authentication_confirmed,
        },
        "note": "Live Composio connection status is shown only on the authenticated Integrations page and is checked before each action.",
    }


@app.get("/health/live")
def liveness():
    return {"status": "alive"}


@app.get("/health/ready")
def readiness():
    checks: dict[str, object] = {"database": False, "redis": None}
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception as exc:
        checks["database_error"] = str(exc)[:200]
    if settings.redis_url:
        try:
            from redis import Redis

            checks["redis"] = bool(Redis.from_url(settings.redis_url, socket_timeout=2).ping())
        except Exception as exc:
            checks["redis"] = False
            checks["redis_error"] = str(exc)[:200]
    ready = checks.get("database") is True and checks.get("redis") is not False
    return JSONResponse({"status": "ready" if ready else "not_ready", "checks": checks}, status_code=200 if ready else 503)


@app.get("/metrics")
def prometheus_metrics(request: Request):
    if not settings.metrics_enabled:
        raise HTTPException(404, "Metrics are disabled")
    supplied = request.headers.get("x-metrics-token", "")
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        supplied = authorization.removeprefix("Bearer ").strip()
    if not settings.metrics_token or supplied != settings.metrics_token:
        raise HTTPException(401, "Invalid metrics token")
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_confirmation(request: Request, token: str):
    lead_id = verify_unsubscribe_token(token)
    if lead_id is None:
        return _private_page_headers(HTMLResponse("<h1>Invalid opt-out link</h1>", status_code=400))
    # GET does not opt out: security scanners often prefetch links.
    response = templates.TemplateResponse(
        request=request,
        name="unsubscribe.html",
        context={
            "request": request,
            "token": token,
            "confirmed": False,
            "sender_name": settings.sender_name,
            "sender_role": settings.sender_role,
        },
    )
    return _private_page_headers(response)


@app.post("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_one_click(request: Request, token: str):
    lead_id = verify_unsubscribe_token(token)
    if lead_id is None:
        return _private_page_headers(HTMLResponse("<h1>Invalid opt-out link</h1>", status_code=400))
    applied = unsubscribe_lead(lead_id, source="one_click_web")
    response = templates.TemplateResponse(
        request=request,
        name="unsubscribe.html",
        context={
            "request": request,
            "token": token,
            "confirmed": True,
            "applied": applied,
            "sender_name": settings.sender_name,
            "sender_role": settings.sender_role,
        },
        status_code=200 if applied else 404,
    )
    return _private_page_headers(response)


@app.get("/r/{token}", response_class=HTMLResponse)
def public_audit_landing(request: Request, token: str):
    if not settings.landing_pages_enabled:
        raise HTTPException(404, "Landing pages are disabled")
    lead_id = verify_landing_token(token)
    if lead_id is None:
        raise HTTPException(404, "Invalid report link")
    with session_scope() as session:
        resolved = AffiliateService().landing_for_lead(session, lead_id)
        if not resolved:
            raise HTTPException(404, "Report is not published")
        lead, business, landing = resolved
        content = dict(landing.content or {})
    response = templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={
            "request": request,
            "lead": lead,
            "business": business,
            "landing": landing,
            "content": content,
            "token": token,
            "unsubscribe_token": unsubscribe_token(lead.id),
            "sender_name": settings.sender_name,
            "sender_role": settings.sender_role,
        },
    )
    return _private_page_headers(response)


@app.get("/go/{token}")
def affiliate_redirect(request: Request, token: str):
    lead_id = verify_landing_token(token)
    if lead_id is None:
        raise HTTPException(404, "Invalid affiliate link")
    with session_scope() as session:
        resolved = AffiliateService().landing_for_lead(session, lead_id)
        if not resolved:
            raise HTTPException(404, "Report is not published")
        _, business, landing = resolved
        AffiliateService().record_click(session, request, business, landing)
    return _private_page_headers(RedirectResponse(settings.affiliate_url, status_code=302))


@admin.post("/mode/{mode}")
def switch_mode(mode: str, csrf: str = Form(...)):
    check_csrf(csrf)
    if mode not in {"demo", "live"}:
        raise HTTPException(400, "Invalid mode")
    if mode == "live":
        if settings.sending_enabled:
            return redirect("/", error="Turn SENDING_ENABLED off before switching into Live mode for the first time.")
        if not settings.admin_security_ready:
            return redirect("/", error="Live mode requires a strong APP_SECRET and ADMIN_PASSWORD.")
        if not settings.composio_ready:
            return redirect("/", error="Live mode requires COMPOSIO_API_KEY and COMPOSIO_USER_ID.")
        try:
            ComposioGateway().resolve_connection_id("google_maps")
        except Exception as exc:
            return redirect("/", error=f"Google Maps is not ready for Live mode: {exc}")
    set_runtime_mode(mode)
    with session_scope() as session:
        record_log(session, "Runtime mode changed", context={"mode": mode})
    message = (
        "Live mode enabled. Sending remains disabled; run a small discovery batch and review sources."
        if mode == "live"
        else "Demo mode enabled. Live leads remain stored but are hidden from the current view."
    )
    return redirect("/", message=message)


@admin.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    runtime_mode = get_runtime_mode()
    with session_scope() as session:
        counts = dict(
            session.execute(
                select(Lead.status, func.count(Lead.id))
                .where(Lead.data_mode == runtime_mode)
                .group_by(Lead.status)
            ).all()
        )
        due_count = session.scalar(
            select(func.count(Message.id)).where(
                Message.status.in_(["scheduled", "retry"]),
                Message.lead.has(Lead.data_mode == runtime_mode),
            )
        ) or 0
        sent_count = session.scalar(
            select(func.count(Message.id)).where(
                Message.status == "sent",
                Message.lead.has(Lead.data_mode == runtime_mode),
            )
        ) or 0
        dnc_count = session.scalar(select(func.count(DoNotContact.id))) or 0
        converted_count = session.scalar(
            select(func.count(Lead.id)).where(
                Lead.data_mode == runtime_mode,
                Lead.conversion_status == "converted",
            )
        ) or 0
        affiliate_summary = AffiliateService().summary(session)
        estimated_open_rate = session.scalar(
            select(func.avg(EmailRecord.estimated_open_rate)).where(
                EmailRecord.estimated_open_rate.is_not(None)
            )
        )
        jobs = session.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(10)).all()
        job_messages = {
            job.id: (_parse_job_summary(job.summary).get("message") or job.summary[:300])
            for job in jobs
        }
        job_errors = {job.id: _safe_job_error(job.error) for job in jobs}
        discovery_job = _discovery_job_payload(_latest_discovery_job(session))
        discovery_running = bool(discovery_job and discovery_job["status"] == "running")
        top_leads = session.scalars(
            select(Lead)
            .where(Lead.data_mode == runtime_mode, Lead.status == "pending_approval")
            .order_by(Lead.weighted_score.desc())
            .limit(8)
        ).all()
    return templates.TemplateResponse(request=request, name="dashboard.html", context=context(
        request, counts=counts, due_count=due_count, sent_count=sent_count, dnc_count=dnc_count,
        converted_count=converted_count, jobs=jobs, job_messages=job_messages, job_errors=job_errors,
        discovery_job=discovery_job, discovery_running=discovery_running,
        top_leads=top_leads, affiliate_summary=affiliate_summary,
        estimated_open_rate=float(estimated_open_rate or 0),
        send_ready=settings.production_send_ready_for(runtime_mode == "demo"),
        message=request.query_params.get("message"), error=request.query_params.get("error"),
    ))


@admin.get("/jobs/discovery/status")
def discovery_status():
    with session_scope() as session:
        payload = _discovery_job_payload(_latest_discovery_job(session))
    return JSONResponse(
        {"job": payload},
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@admin.get("/integrations", response_class=HTMLResponse)
def integrations_page(request: Request):
    report = {toolkit: {"active": False, "connection_id": "", "error": "Composio is not configured"} for toolkit in TOOLKITS}
    active_connections = []
    if settings.composio_ready:
        try:
            gateway = ComposioGateway()
            report = gateway.connection_report()
            active_connections = gateway.active_connections()
        except Exception as exc:
            report["_error"] = {"active": False, "connection_id": "", "error": str(exc)}
    return templates.TemplateResponse(request=request, name="integrations.html", context=context(
        request,
        report=report,
        active_connections=active_connections,
        ai_report=ai_backend_report(demo_mode=get_runtime_mode() == "demo"),
        message=request.query_params.get("message"),
        error=request.query_params.get("error"),
    ))


@admin.post("/integrations/connect/{toolkit}")
def connect_integration(toolkit: str, csrf: str = Form(...)):
    check_csrf(csrf)
    if toolkit not in TOOLKITS:
        raise HTTPException(400, "Unsupported toolkit")
    try:
        connect_url = ComposioGateway().create_connect_link(toolkit)
        return RedirectResponse(connect_url, status_code=303)
    except (RuntimeError, ValueError) as exc:
        return redirect("/integrations", error=str(exc))


@admin.post("/jobs/sheet")
def manual_sheet_sync(background: BackgroundTasks, csrf: str = Form(...)):
    check_csrf(csrf)
    background.add_task(dispatch_job, "sync_sheet")
    return redirect("/integrations", "Google Sheets sync started.")


@admin.get("/leads", response_class=HTMLResponse)
def leads_page(request: Request, status: str = "", q: str = "", mode: str = ""):
    selected_mode = mode if mode in {"demo", "live", "all"} else get_runtime_mode()
    with session_scope() as session:
        query = select(Lead)
        if selected_mode != "all":
            query = query.where(Lead.data_mode == selected_mode)
        if status:
            query = query.where(Lead.status == status)
        if q:
            query = query.where(Lead.business_name.ilike(f"%{q}%"))
        leads = session.scalars(query.order_by(Lead.weighted_score.desc(), Lead.created_at.desc()).limit(500)).all()
        statuses = [x[0] for x in session.execute(select(Lead.status).distinct()).all()]
    return templates.TemplateResponse(request=request, name="leads.html", context=context(
        request,
        leads=leads,
        statuses=sorted(statuses),
        selected_status=status,
        selected_mode=selected_mode,
        q=q,
        message=request.query_params.get("message"), error=request.query_params.get("error"),
    ))


@admin.get("/leads/{lead_id}", response_class=HTMLResponse)
def lead_detail(request: Request, lead_id: int):
    with session_scope() as session:
        lead = session.scalar(
            select(Lead).options(selectinload(Lead.messages), selectinload(Lead.evidence)).where(Lead.id == lead_id)
        )
        if not lead:
            raise HTTPException(404, "Lead not found")
    public_report_url = (
        f"{settings.report_base_url}/r/{landing_token(lead.id)}"
        if lead.data_mode == "live"
        else ""
    )
    return templates.TemplateResponse(request=request, name="lead_detail.html", context=context(
        request,
        lead=lead,
        public_report_url=public_report_url,
        message=request.query_params.get("message"),
        error=request.query_params.get("error"),
    ))


@admin.get("/leads/{lead_id}/screenshot")
def lead_screenshot(lead_id: int):
    with session_scope() as session:
        lead = session.get(Lead, lead_id)
        if not lead:
            raise HTTPException(404, "Lead not found")
        raw_path = (lead.audit_facts or {}).get("screenshot_path")
    if not raw_path:
        raise HTTPException(404, "No mobile screenshot is available")
    allowed_root = Path(settings.browser_screenshot_dir).expanduser().resolve()
    screenshot = Path(raw_path).expanduser().resolve()
    if not screenshot.is_relative_to(allowed_root) or not screenshot.is_file():
        raise HTTPException(404, "Screenshot is unavailable")
    return FileResponse(screenshot, media_type="image/png")


@admin.post("/leads/{lead_id}/approve")
def approve(lead_id: int, csrf: str = Form(...)):
    check_csrf(csrf)
    try:
        approve_lead(lead_id)
        return redirect(f"/leads/{lead_id}", "Lead approved; messages scheduled behind the send gate.")
    except ValueError as exc:
        return redirect(f"/leads/{lead_id}", error=str(exc))


@admin.post("/leads/{lead_id}/reject")
def reject(lead_id: int, csrf: str = Form(...), reason: str = Form("")):
    check_csrf(csrf)
    try:
        reject_lead(lead_id, reason)
        return redirect(f"/leads/{lead_id}", "Lead rejected and future messages cancelled.")
    except ValueError as exc:
        return redirect(f"/leads/{lead_id}", error=str(exc))


@admin.post("/leads/{lead_id}/outcome")
def update_outcome(
    lead_id: int,
    csrf: str = Form(...),
    conversion_status: str = Form(...),
    conversion_notes: str = Form(""),
):
    check_csrf(csrf)
    allowed = {"not_started", "interested", "maybe_later", "converted", "not_converted"}
    if conversion_status not in allowed:
        raise HTTPException(400, "Invalid conversion status")
    with session_scope() as session:
        lead = session.scalar(
            select(Lead).options(selectinload(Lead.messages)).where(Lead.id == lead_id)
        )
        if not lead:
            raise HTTPException(404, "Lead not found")
        lead.conversion_status = conversion_status
        lead.conversion_notes = conversion_notes.strip()
        if conversion_status == "converted":
            cancel_future_messages(session, lead, "Cancelled after conversion")
        sync_business_from_legacy(session, lead)
        for message in lead.messages:
            sync_email_from_legacy(session, lead, message)
    return redirect(f"/leads/{lead_id}", "Conversion outcome updated.")


@admin.post("/leads/{lead_id}/reverify")
def reverify(lead_id: int, background: BackgroundTasks, csrf: str = Form(...)):
    check_csrf(csrf)
    background.add_task(reverify_lead, lead_id)
    return redirect(f"/leads/{lead_id}", "Reverification started in the background.")


@admin.post("/messages/{message_id}/edit")
def edit_message(
    message_id: int,
    csrf: str = Form(...),
    subject: str = Form(...),
    body_core: str = Form(...),
):
    check_csrf(csrf)
    subject = subject.strip()
    body_core = body_core.strip()
    if not subject or not body_core:
        return redirect("/leads", error="Subject and value-first message text are required.")
    if settings.affiliate_url in body_core:
        return redirect("/leads", error="Do not paste the affiliate URL into the editable text; LeadFlow inserts it after value with disclosure.")
    with session_scope() as session:
        item = session.scalar(select(Message).options(selectinload(Message.lead)).where(Message.id == message_id))
        if not item:
            raise HTTPException(404, "Message not found")
        lead_id = item.lead_id
        if item.status not in {"draft", "scheduled", "retry"}:
            return redirect(f"/leads/{lead_id}", error=f"A message with status {item.status!r} cannot be edited.")
        item.subject = subject[:500]
        item.body_core = body_core
        item.body_final = compose_final_body(item.lead, item, allow_postal_placeholder=not settings.postal_ready)
    return redirect(f"/leads/{lead_id}", "Draft saved. System-managed disclosure, opt-out and address were preserved.")


@admin.get("/queue", response_class=HTMLResponse)
def queue_page(request: Request):
    runtime_mode = get_runtime_mode()
    with session_scope() as session:
        messages = session.scalars(
            select(Message).options(selectinload(Message.lead)).where(
                Message.status.in_(["scheduled", "retry", "sent", "failed", "cancelled"]),
                Message.lead.has(Lead.data_mode == runtime_mode),
            ).order_by(Message.planned_at.desc()).limit(500)
        ).all()
    return templates.TemplateResponse(request=request, name="queue.html", context=context(
        request, messages=messages, message=request.query_params.get("message"), error=request.query_params.get("error"),
    ))


@admin.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    with session_scope() as session:
        users = session.scalars(select(User).order_by(User.created_at)).all()
        tasks = session.scalars(select(TaskRecord).order_by(TaskRecord.queued_at.desc()).limit(50)).all()
        logs = session.scalars(select(LogRecord).order_by(LogRecord.created_at.desc()).limit(50)).all()
        affiliate_summary = AffiliateService().summary(session)
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context=context(
            request,
            users=users,
            tasks=tasks,
            logs=logs,
            affiliate_summary=affiliate_summary,
            message=request.query_params.get("message"),
            error=request.query_params.get("error"),
        ),
    )


@admin.get("/campaign", response_class=HTMLResponse)
def campaign_page(request: Request):
    with session_scope() as session:
        campaign = session.scalar(select(Campaign).limit(1))
    return templates.TemplateResponse(request=request, name="campaign.html", context=context(
        request, campaign=campaign, message=request.query_params.get("message"), error=request.query_params.get("error"),
    ))


@admin.post("/campaign")
def update_campaign(
    csrf: str = Form(...), name: str = Form(...), active: str | None = Form(None),
    locations: str = Form(...), industries: str = Form(...), daily_limit: int = Form(...),
    minimum_score: float = Form(...), discovery_hour_utc: int = Form(...),
):
    check_csrf(csrf)
    with session_scope() as session:
        campaign = session.scalar(select(Campaign).limit(1))
        campaign.name = name.strip()
        campaign.active = active == "on"
        campaign.locations = [x.strip() for x in locations.splitlines() if x.strip()]
        campaign.industries = [x.strip() for x in industries.splitlines() if x.strip()]
        campaign.daily_limit = max(1, min(50, daily_limit))
        campaign.minimum_score = max(1, min(10, minimum_score))
        campaign.discovery_hour_utc = max(0, min(23, discovery_hour_utc))
    return redirect("/campaign", "Campaign settings saved.")


@admin.post("/jobs/discover")
def manual_discover(background: BackgroundTasks, csrf: str = Form(...)):
    check_csrf(csrf)
    background.add_task(discover_job, True)
    return redirect("/", "Discovery job started.")


@admin.post("/jobs/fast-start")
def manual_fast_start(background: BackgroundTasks, csrf: str = Form(...)):
    check_csrf(csrf)
    # A human-triggered run intentionally bypasses the startup cooldown. The
    # discovery lock still prevents duplicate concurrent runs in this process.
    background.add_task(fast_start_job, True)
    return redirect("/", f"Fast-start discovery started for up to {settings.startup_discovery_target} candidates.")


@admin.post("/jobs/send")
def manual_send(background: BackgroundTasks, csrf: str = Form(...)):
    check_csrf(csrf)
    background.add_task(dispatch_job, "send_due")
    return redirect("/", "Send-queue job started; safety gates still apply.")


@admin.post("/jobs/sync")
def manual_sync(background: BackgroundTasks, csrf: str = Form(...)):
    check_csrf(csrf)
    background.add_task(sync_replies_job)
    return redirect("/", "Gmail reply sync started.")


def _csv_response(filename: str, headers: list[str], rows: list[list]):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows(rows)
    payload = iter([stream.getvalue()])
    return StreamingResponse(payload, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@admin.get("/reports/leads.csv")
def leads_csv():
    with session_scope() as session:
        leads = session.scalars(select(Lead).order_by(Lead.weighted_score.desc())).all()
        rows = [[
            x.id, x.business_name, x.industry, x.city, x.state, x.website_url or "", x.contact_email or x.contact_page or "",
            x.website_status, " | ".join(x.issues or []), x.need_score, x.ability_score, x.growth_score, x.response_score,
            x.weighted_score, x.status, x.data_mode, x.conversion_status, x.conversion_notes,
            x.created_at.isoformat(), x.last_verified_at.isoformat() if x.last_verified_at else "",
        ] for x in leads]
    return _csv_response(
        "leadflow-leads.csv",
        ["ID","Business","Industry","City","State","Website","Contact","Website Status","Problems","Need","Ability","Growth","Response","Weighted Score","Status","Data Mode","Conversion Status","Conversion Notes","Created","Last Verified"],
        rows,
    )


@admin.get("/reports/outreach.csv")
def outreach_csv():
    with session_scope() as session:
        messages = session.scalars(select(Message).options(selectinload(Message.lead)).order_by(Message.lead_id, Message.stage)).all()
        rows = [[
            x.lead_id, x.lead.business_name, x.stage, x.day_offset, x.subject, x.body_final, x.status,
            x.planned_at.isoformat() if x.planned_at else "", x.sent_at.isoformat() if x.sent_at else "", x.error or "",
        ] for x in messages]
    return _csv_response("leadflow-outreach.csv", ["Lead ID","Business","Stage","Day Offset","Subject","Body","Status","Planned","Sent","Error"], rows)


app.include_router(admin)
