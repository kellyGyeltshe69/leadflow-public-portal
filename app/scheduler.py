from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import get_settings
from .pipeline import fast_start_job, maybe_discover_job, send_due_job, sync_replies_job
from .sheets import sync_google_sheet_job

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    settings = get_settings()
    if not settings.scheduler_enabled or _scheduler:
        return _scheduler
    scheduler = BackgroundScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300})
    scheduler.add_job(maybe_discover_job, IntervalTrigger(minutes=settings.discovery_check_minutes), id="discover", replace_existing=True)
    scheduler.add_job(send_due_job, IntervalTrigger(minutes=settings.send_queue_minutes), id="send_due", replace_existing=True)
    scheduler.add_job(sync_replies_job, IntervalTrigger(minutes=settings.reply_sync_minutes), id="sync_replies", replace_existing=True)
    scheduler.add_job(sync_google_sheet_job, IntervalTrigger(minutes=settings.sheet_sync_minutes), id="sync_sheet", replace_existing=True)
    now = datetime.now(timezone.utc)
    if settings.fast_start_enabled:
        scheduler.add_job(
            fast_start_job,
            DateTrigger(run_date=now + timedelta(seconds=5)),
            id="startup_discovery",
            replace_existing=True,
        )
    if settings.startup_send_approved:
        scheduler.add_job(
            send_due_job,
            DateTrigger(run_date=now + timedelta(seconds=10)),
            id="startup_send_approved",
            replace_existing=True,
        )
    scheduler.start()
    _scheduler = scheduler
    log.info("LeadFlow scheduler started")
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
