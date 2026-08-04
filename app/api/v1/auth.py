from __future__ import annotations

import secrets

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...core.auth import create_access_token, hash_password, mark_login, verify_password
from ...database.saas_models import User
from ..deps import current_user, get_db

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()
oauth = OAuth()
if settings.google_oauth_enabled and settings.google_oauth_client_id and settings.google_oauth_client_secret:
    oauth.register(
        name="google",
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int


class UserResponse(BaseModel):
    id: int
    email: str
    username: str
    role: str


@router.post("/token", response_model=TokenResponse)
def login(form: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_db)):
    identifier = form.username.strip().lower()
    user = session.scalar(
        select(User).where(or_(User.username == form.username, User.email == identifier))
    )
    if not user or not user.is_active or not verify_password(form.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_access_token(user)
    mark_login(user.id)
    return TokenResponse(access_token=token, expires_in_seconds=get_settings().jwt_access_minutes * 60)


@router.get("/google/login")
async def google_login(request: Request):
    if not settings.google_oauth_enabled or not getattr(oauth, "google", None):
        raise HTTPException(503, "Google OAuth is not configured")
    redirect_uri = settings.google_oauth_redirect_uri or str(request.url_for("google_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback", name="google_callback", response_model=TokenResponse)
async def google_callback(request: Request, session: Session = Depends(get_db)):
    if not settings.google_oauth_enabled or not getattr(oauth, "google", None):
        raise HTTPException(503, "Google OAuth is not configured")
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as exc:
        raise HTTPException(401, "Google OAuth authorization failed") from exc
    userinfo = token.get("userinfo") or {}
    email = str(userinfo.get("email") or "").lower()
    subject = str(userinfo.get("sub") or "")
    if not email or not subject or not userinfo.get("email_verified", False):
        raise HTTPException(401, "Verified Google email was not returned")
    user = session.scalar(
        select(User).where(
            or_(
                (User.oauth_provider == "google") & (User.oauth_subject == subject),
                User.email == email,
            )
        )
    )
    if not user:
        base_username = email.split("@", 1)[0][:100]
        username = base_username
        suffix = 1
        while session.scalar(select(User.id).where(User.username == username)):
            suffix += 1
            username = f"{base_username}-{suffix}"
        user = User(
            email=email,
            username=username,
            password_hash=hash_password(secrets.token_urlsafe(32)),
            role="viewer",
            oauth_provider="google",
            oauth_subject=subject,
            is_active=True,
        )
        session.add(user)
        session.flush()
    else:
        user.oauth_provider = "google"
        user.oauth_subject = subject
    access_token = create_access_token(user)
    session.commit()
    return TokenResponse(access_token=access_token, expires_in_seconds=settings.jwt_access_minutes * 60)


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(current_user)):
    return UserResponse(id=user.id, email=user.email, username=user.username, role=user.role)
