from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash
from sqlalchemy import select

from ..config import get_settings
from ..database.saas_models import User
from ..db import session_scope
from ..models import utcnow

password_hash = PasswordHash.recommended()
ROLE_ORDER = {"viewer": 1, "marketer": 2, "admin": 3}


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return password_hash.verify(password, encoded)
    except Exception:
        return False


def create_access_token(user: User) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "username": user.username,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_minutes),
        "iss": "leadflow",
        "aud": "leadflow-api",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        issuer="leadflow",
        audience="leadflow-api",
    )


def role_allows(actual: str, required: str) -> bool:
    return ROLE_ORDER.get(actual, 0) >= ROLE_ORDER.get(required, 999)


def bootstrap_admin_user() -> int:
    settings = get_settings()
    email = settings.admin_email.strip().lower()
    with session_scope() as session:
        user = session.scalar(select(User).where(User.username == settings.admin_username))
        if not user:
            user = User(
                username=settings.admin_username,
                email=email,
                password_hash=hash_password(settings.admin_password),
                role="admin",
                is_active=True,
            )
            session.add(user)
            session.flush()
        elif not verify_password(settings.admin_password, user.password_hash):
            # Environment remains the recovery source for the single bootstrap
            # administrator. Additional users are managed through the admin API.
            user.password_hash = hash_password(settings.admin_password)
        return user.id


def mark_login(user_id: int) -> None:
    with session_scope() as session:
        user = session.get(User, user_id)
        if user:
            user.last_login_at = utcnow()
