from __future__ import annotations

from sqlalchemy.orm import Session

from ..core.middleware import request_id_ctx
from ..database.saas_models import LogRecord


def record_log(
    session: Session,
    message: str,
    *,
    level: str = "INFO",
    logger: str = "leadflow.audit",
    context: dict | None = None,
) -> LogRecord:
    record = LogRecord(
        level=level,
        logger=logger,
        message=message,
        correlation_id=request_id_ctx.get() or None,
        context=context or {},
    )
    session.add(record)
    return record
