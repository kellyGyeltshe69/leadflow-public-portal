from __future__ import annotations

from collections.abc import Generator

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from ..core.auth import decode_access_token, role_allows
from ..database.saas_models import User
from ..db import SessionLocal

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def current_user(token: str = Depends(oauth2_scheme), session: Session = Depends(get_db)) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired access token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise credentials_error from exc
    user = session.get(User, user_id)
    if not user or not user.is_active:
        raise credentials_error
    return user


def require_role(required: str):
    def dependency(user: User = Depends(current_user)) -> User:
        if not role_allows(user.role, required):
            raise HTTPException(status_code=403, detail=f"Role {required} or higher is required")
        return user

    return dependency
