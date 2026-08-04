from __future__ import annotations

import signal
import time
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..config import get_settings
from ..core.logging import configure_logging
from ..db import init_db
from .queue import dispatch_job


def main() -> None:
    configure_logging(json_logs=True)
    settings = get_settings()
    init_db()
    scheduler = BackgroundScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1})
    scheduler.add_job(dispatch_job, IntervalTrigger(minutes=settings.discovery_check_minutes), args=["discover"], id="discover")
    scheduler.add_job(dispatch_job, IntervalTrigger(minutes=settings.send_queue_minutes), args=["send_due"], id="send")
    scheduler.add_job(dispatch_job, IntervalTrigger(minutes=settings.reply_sync_minutes), args=["sync_replies"], id="replies")
    scheduler.add_job(dispatch_job, IntervalTrigger(minutes=settings.sheet_sync_minutes), args=["sync_sheet"], id="sheet")
    now = datetime.now(timezone.utc)
    if settings.fast_start_enabled:
        scheduler.add_job(dispatch_job, DateTrigger(now + timedelta(seconds=5)), args=["fast_start"], id="startup_discover")
    if settings.startup_send_approved:
        scheduler.add_job(dispatch_job, DateTrigger(now + timedelta(seconds=10)), args=["send_due"], id="startup_send")
    scheduler.start()
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while not stopping:
        time.sleep(1)
    scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
