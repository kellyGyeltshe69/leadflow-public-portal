from __future__ import annotations

import logging
from typing import Any

from redis import Redis
from rq import Queue

from ..config import get_settings
from ..database.saas_models import TaskRecord
from ..db import session_scope
from ..models import utcnow

log = logging.getLogger(__name__)


def _job_function(name: str):
    from ..pipeline import (
        discover_job,
        fast_start_job,
        reverify_lead,
        send_due_job,
        sync_replies_job,
    )
    from ..sheets import sync_google_sheet_job

    functions = {
        "discover": discover_job,
        "fast_start": fast_start_job,
        "reverify": reverify_lead,
        "send_due": send_due_job,
        "sync_replies": sync_replies_job,
        "sync_sheet": sync_google_sheet_job,
    }
    if name not in functions:
        raise ValueError(f"Unknown job: {name}")
    return functions[name]


def execute_task(task_id: int, name: str, args: list, kwargs: dict) -> Any:
    with session_scope() as session:
        task = session.get(TaskRecord, task_id)
        if task:
            task.status = "running"
            task.started_at = utcnow()
            task.attempts += 1
    try:
        result = _job_function(name)(*args, **kwargs)
        with session_scope() as session:
            task = session.get(TaskRecord, task_id)
            if task:
                task.status = "completed"
                task.result = result if isinstance(result, dict) else {"result": str(result)}
                task.finished_at = utcnow()
        return result
    except Exception as exc:
        log.exception("Task %s failed", name)
        with session_scope() as session:
            task = session.get(TaskRecord, task_id)
            if task:
                task.status = "failed"
                task.error = str(exc)
                task.finished_at = utcnow()
        raise


def dispatch_job(name: str, *args, **kwargs) -> dict:
    settings = get_settings()
    with session_scope() as session:
        task = TaskRecord(task_type=name, payload={"args": list(args), "kwargs": kwargs})
        session.add(task)
        session.flush()
        task_id = task.id
    if settings.async_workers_enabled and settings.redis_url:
        queue = Queue("leadflow", connection=Redis.from_url(settings.redis_url))
        job = queue.enqueue(execute_task, task_id, name, list(args), kwargs, job_timeout="2h", result_ttl=86400)
        with session_scope() as session:
            task = session.get(TaskRecord, task_id)
            if task:
                task.queue_job_id = job.id
        return {"mode": "queued", "task_id": task_id, "job_id": job.id}
    result = execute_task(task_id, name, list(args), kwargs)
    return {"mode": "inline", "task_id": task_id, "result": result}
