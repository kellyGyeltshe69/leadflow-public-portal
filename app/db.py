from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
connect_args = {"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {}
engine_options = {
    "connect_args": connect_args,
    "future": True,
    "pool_pre_ping": True,
    "pool_recycle": settings.database_pool_recycle_seconds,
}
if not settings.database_url.startswith("sqlite"):
    engine_options.update({
        "pool_size": settings.database_pool_size,
        "max_overflow": settings.database_max_overflow,
    })
engine = create_engine(settings.database_url, **engine_options)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False, future=True)


if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _apply_sqlite_compat_migrations() -> None:
    """Small in-place upgrades for databases created by LeadFlow 0.1/0.2.

    New deployments should eventually use Alembic/PostgreSQL, but these fixed,
    idempotent ALTER statements keep the downloadable SQLite MVP upgradeable.
    """
    if not settings.database_url.startswith("sqlite"):
        return
    additions = {
        "leads": {
            "data_mode": "VARCHAR(20) NOT NULL DEFAULT 'legacy'",
            "conversion_status": "VARCHAR(50) NOT NULL DEFAULT 'not_started'",
            "conversion_notes": "TEXT NOT NULL DEFAULT ''",
        },
        "evidence": {
            "confidence": "VARCHAR(20) NOT NULL DEFAULT 'medium'",
        },
        "campaigns": {
            "slug": "VARCHAR(180)",
            "owner_id": "INTEGER",
            "status": "VARCHAR(30) NOT NULL DEFAULT 'active'",
            "template": "JSON NOT NULL DEFAULT '{}'",
            "schedule": "JSON NOT NULL DEFAULT '{}'",
            "cloned_from_id": "INTEGER",
            "archived_at": "DATETIME",
        },
    }
    with engine.begin() as connection:
        table_names = {
            row[0]
            for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
        for table_name, columns in additions.items():
            if table_name not in table_names:
                continue
            existing = {
                row[1]
                for row in connection.execute(text(f"PRAGMA table_info({table_name})"))
            }
            for column_name, definition in columns.items():
                if column_name not in existing:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"))
        if "leads" in table_names:
            initial_mode = "demo" if settings.demo_mode else "live"
            connection.execute(
                text("UPDATE leads SET data_mode = :mode WHERE data_mode IS NULL OR data_mode = 'legacy'"),
                {"mode": initial_mode},
            )


def init_db() -> None:
    from . import models  # noqa: F401
    from .database import saas_models  # noqa: F401
    if settings.auto_create_schema:
        Base.metadata.create_all(engine)
    _apply_sqlite_compat_migrations()
