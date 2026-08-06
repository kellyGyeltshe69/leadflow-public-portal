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


BASE36_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
SHORT_TOKEN_VERSION = "s1"
SHORT_SIGNATURE_BYTES = 16  # 128-bit HMAC tag


def _base36_encode(value: int) -> str:
    if value < 0:
        raise ValueError("value must be non-negative")
    if value == 0:
        return "0"
    encoded = ""
    while value:
        value, remainder = divmod(value, 36)
        encoded = BASE36_ALPHABET[remainder] + encoded
    return encoded


def _base36_decode(value: str) -> int:
    if not value or any(character not in BASE36_ALPHABET for character in value):
        raise ValueError("invalid base36 value")
    return int(value, 36)


def _short_signed_id(object_id: int, purpose: str) -> str:
    encoded_id = _base36_encode(object_id)
    digest = bytes.fromhex(_sign(str(object_id), purpose))[:SHORT_SIGNATURE_BYTES]
    signature = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return f"{SHORT_TOKEN_VERSION}.{encoded_id}.{signature}"


def unsubscribe_token(lead_id: int) -> str:
    return _short_signed_id(lead_id, "unsubscribe")


def landing_token(lead_id: int) -> str:
    return _short_signed_id(lead_id, "landing")


def verify_landing_token(token: str) -> int | None:
    return _verify_signed_id(token, "landing")


def _verify_short_signed_id(token: str, purpose: str) -> int | None:
    try:
        version, encoded_id, supplied_signature = token.split(".", 2)
        if version != SHORT_TOKEN_VERSION:
            return None
        object_id = _base36_decode(encoded_id)
        expected = _short_signed_id(object_id, purpose).rsplit(".", 1)[1]
        if not secrets.compare_digest(supplied_signature, expected):
            return None
        return object_id
    except ValueError:
        return None


def _verify_legacy_signed_id(token: str, purpose: str) -> int | None:
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        object_id_text, signature = raw.split(".", 1)
        if not secrets.compare_digest(signature, _sign(object_id_text, purpose)):
            return None
        return int(object_id_text)
    except (ValueError, UnicodeDecodeError):
        return None


def _verify_signed_id(token: str, purpose: str) -> int | None:
    if token.startswith(SHORT_TOKEN_VERSION + "."):
        return _verify_short_signed_id(token, purpose)
    return _verify_legacy_signed_id(token, purpose)


def verify_unsubscribe_token(token: str) -> int | None:
    return _verify_signed_id(token, "unsubscribe")
