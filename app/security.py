from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import get_settings

basic = HTTPBasic()


def require_admin(credentials: HTTPBasicCredentials = Depends(basic)) -> str:
    settings = get_settings()
    username_ok = secrets.compare_digest(credentials.username.encode(), settings.admin_username.encode())
    password_ok = secrets.compare_digest(credentials.password.encode(), settings.admin_password.encode())
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid administrator credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _sign(value: str, purpose: str) -> str:
    key = get_settings().app_secret.encode()
    return hmac.new(key, f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def csrf_token() -> str:
    return _sign("admin", "csrf")


def verify_csrf(token: str) -> bool:
    return secrets.compare_digest(token or "", csrf_token())


def unsubscribe_token(lead_id: int) -> str:
    payload = str(lead_id)
    signature = _sign(payload, "unsubscribe")
    raw = f"{payload}.{signature}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def landing_token(lead_id: int) -> str:
    payload = str(lead_id)
    signature = _sign(payload, "landing")
    return base64.urlsafe_b64encode(f"{payload}.{signature}".encode()).decode().rstrip("=")


def verify_landing_token(token: str) -> int | None:
    return _verify_signed_id(token, "landing")


def _verify_signed_id(token: str, purpose: str) -> int | None:
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        object_id_text, signature = raw.split(".", 1)
        if not secrets.compare_digest(signature, _sign(object_id_text, purpose)):
            return None
        return int(object_id_text)
    except (ValueError, UnicodeDecodeError):
        return None


def verify_unsubscribe_token(token: str) -> int | None:
    return _verify_signed_id(token, "unsubscribe")
