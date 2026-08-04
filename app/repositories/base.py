from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

T = TypeVar("T")


class Repository(Generic[T]):
    def __init__(self, session: Session, model: type[T]):
        self.session = session
        self.model = model

    def get(self, object_id: int) -> T | None:
        return self.session.get(self.model, object_id)

    def list(self, *, offset: int = 0, limit: int = 100) -> list[T]:
        limit = max(1, min(limit, 500))
        return list(self.session.scalars(select(self.model).offset(offset).limit(limit)).all())

    def add(self, value: T) -> T:
        self.session.add(value)
        self.session.flush()
        return value

    def delete(self, value: T) -> None:
        self.session.delete(value)
