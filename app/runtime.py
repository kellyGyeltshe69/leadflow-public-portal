from __future__ import annotations

from sqlalchemy import select

from .config import get_settings
from .db import session_scope
from .models import SystemState

VALID_MODES = {"demo", "live"}


def ensure_system_state() -> str:
    settings = get_settings()
    with session_scope() as session:
        state = session.get(SystemState, 1)
        if not state:
            state = SystemState(id=1, mode="demo" if settings.demo_mode else "live")
            session.add(state)
            session.flush()
        return state.mode


def get_runtime_mode() -> str:
    with session_scope() as session:
        mode = session.scalar(select(SystemState.mode).where(SystemState.id == 1))
    if mode not in VALID_MODES:
        return ensure_system_state()
    return mode


def set_runtime_mode(mode: str) -> str:
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid runtime mode: {mode}")
    with session_scope() as session:
        state = session.get(SystemState, 1)
        if not state:
            state = SystemState(id=1, mode=mode)
            session.add(state)
        else:
            state.mode = mode
    return mode


def is_demo_mode() -> bool:
    return get_runtime_mode() == "demo"
