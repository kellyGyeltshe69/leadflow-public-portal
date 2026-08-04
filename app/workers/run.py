from __future__ import annotations

from redis import Redis
from rq import Queue, Worker

from ..config import get_settings
from ..core.logging import configure_logging
from ..db import init_db


def main() -> None:
    configure_logging(json_logs=True)
    settings = get_settings()
    if not settings.redis_url:
        raise SystemExit("REDIS_URL is required for the RQ worker")
    init_db()
    connection = Redis.from_url(settings.redis_url)
    worker = Worker([Queue("leadflow", connection=connection)], connection=connection)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
