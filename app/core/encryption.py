from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from ..config import get_settings


def _fernet() -> Fernet:
    key = get_settings().encryption_key.encode()
    if not key:
        raise RuntimeError("ENCRYPTION_KEY is not configured")
    return Fernet(key)


def encrypt_text(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_text(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Encrypted value cannot be decrypted with the active key") from exc
