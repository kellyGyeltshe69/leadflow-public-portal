from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from .config import get_settings
from .core.middleware import RateLimitMiddleware, RequestContextMiddleware, SecurityHeadersMiddleware
from .db import engine, session_scope
from .models import DoNotContact, Lead
from .security import (
    unsubscribe_token,
    verify_landing_token,
    verify_unsubscribe_token,
)
from .services.affiliate import AffiliateService
from .services.compat_sync import sync_business_from_legacy, sync_email_from_legacy
from .services.public_report import build_public_report
from .utils import normalize_email

settings = get_settings()
templates = Jinja2Templates(directory="app/templates")


def _portal_origin() -> str:
    return (
        os.environ.get("PUBLIC_BASE_URL")
        or os.environ.get("RENDER_EXTERNAL_URL")
        or settings.public_base_url
    ).rstrip("/")


def _private_page_headers(response) -> None:
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Referrer-Policy"] = "no-referrer"


def _apply_unsubscribe(lead_id: int) -> bool:
    with session_scope() as session:
        lead = session.scalar(
            select(Lead).options(selectinload(Lead.messages)).where(Lead.id == lead_id)
        )
        if not lead or lead.data_mode != "live":
            return False
        lead.status = "opted_out"
        email = normalize_email(lead.contact_email or "")
        if email and not session.scalar(select(DoNotContact.id).where(DoNotContact.email == email)):
            session.add(
                DoNotContact(
                    email=email,
                    reason="opt_out",
                    source="public_portal",
                )
            )
        for message in lead.messages:
            if message.status not in {"sent", "cancelled"}:
                message.status = "cancelled"
                message.error = "Cancelled after public-portal opt-out"
            sync_email_from_legacy(session, lead, message)
        sync_business_from_legacy(session, lead)
        return True


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if len(settings.app_secret) < 32 or settings.app_secret.startswith("replace-"):
        raise RuntimeError("The public portal requires the same strong APP_SECRET as local LeadFlow")
    if settings.sending_enabled:
        raise RuntimeError("The public portal must use SENDING_ENABLED=false")
    if not settings.database_url.startswith("postgresql") and os.environ.get("PUBLIC_PORTAL_ALLOW_SQLITE_FOR_TESTS") != "true":
        raise RuntimeError("The public portal requires PostgreSQL (Neon is supported)")
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    yield


app = FastAPI(
    title="LeadFlow Public Report Portal",
    version="1.7.1",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def portal_response_policy(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    if request.url.path.startswith(("/r/", "/go/", "/u/", "/unsubscribe/")):
        _private_page_headers(response)
    return response


@app.get("/health")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "service": "leadflow-public-portal"}
    except Exception:
        raise HTTPException(503, "Database is unavailable") from None


@app.get("/health/live")
def liveness():
    return {"status": "alive"}


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return "User-agent: *\nDisallow: /\n"


@app.get("/", response_class=HTMLResponse)
def portal_home(request: Request):
    response = templates.TemplateResponse(
        request=request,
        name="portal_home.html",
        context={"request": request, "portal_origin": _portal_origin()},
    )
    _private_page_headers(response)
    return response


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
        report = build_public_report(lead, business, landing, content)
    response = templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={
            "request": request,
            "lead": lead,
            "business": business,
            "landing": landing,
            "content": content,
            "report": report,
            "token": token,
            "unsubscribe_token": unsubscribe_token(lead.id),
            "sender_name": settings.sender_name,
            "sender_role": settings.sender_role,
        },
    )
    _private_page_headers(response)
    return response


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
    response = RedirectResponse(settings.affiliate_url, status_code=302)
    _private_page_headers(response)
    return response


@app.get("/u/{token}", response_class=HTMLResponse)
@app.get("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_confirmation(request: Request, token: str):
    lead_id = verify_unsubscribe_token(token)
    if lead_id is None:
        raise HTTPException(400, "Invalid opt-out link")
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
    _private_page_headers(response)
    return response


@app.post("/u/{token}", response_class=HTMLResponse)
@app.post("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_one_click(request: Request, token: str):
    lead_id = verify_unsubscribe_token(token)
    if lead_id is None:
        raise HTTPException(400, "Invalid opt-out link")
    applied = _apply_unsubscribe(lead_id)
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
    _private_page_headers(response)
    return response
