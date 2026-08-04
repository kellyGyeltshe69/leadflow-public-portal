from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.auth import hash_password
from ...database.saas_models import LogRecord, TaskRecord, User
from ..deps import get_db, require_role

router = APIRouter(prefix="/admin", tags=["admin"])


class UserInput(BaseModel):
    email: str
    username: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=16)
    role: str = "viewer"


@router.get("/users")
def users(session: Session = Depends(get_db), _user: User = Depends(require_role("admin"))):
    return [
        {"id": user.id, "email": user.email, "username": user.username, "role": user.role, "active": user.is_active}
        for user in session.scalars(select(User).order_by(User.created_at)).all()
    ]


@router.post("/users", status_code=201)
def create_user(data: UserInput, session: Session = Depends(get_db), _user: User = Depends(require_role("admin"))):
    if data.role not in {"viewer", "marketer", "admin"}:
        raise HTTPException(400, "Invalid role")
    if session.scalar(select(User.id).where((User.email == data.email.lower()) | (User.username == data.username))):
        raise HTTPException(409, "User already exists")
    user = User(
        email=data.email.lower(),
        username=data.username,
        password_hash=hash_password(data.password),
        role=data.role,
    )
    session.add(user)
    session.commit()
    return {"id": user.id, "username": user.username, "role": user.role}


@router.get("/workers")
def workers(session: Session = Depends(get_db), _user: User = Depends(require_role("admin"))):
    counts: dict[str, int] = {
        status: count
        for status, count in session.execute(
            select(TaskRecord.status, func.count(TaskRecord.id)).group_by(TaskRecord.status)
        ).all()
    }
    return {"tasks": counts}


@router.get("/logs")
def logs(limit: int = 100, session: Session = Depends(get_db), _user: User = Depends(require_role("admin"))):
    return [
        {"level": item.level, "logger": item.logger, "message": item.message, "created_at": item.created_at}
        for item in session.scalars(select(LogRecord).order_by(LogRecord.created_at.desc()).limit(min(limit, 500))).all()
    ]
