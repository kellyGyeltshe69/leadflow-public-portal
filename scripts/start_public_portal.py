from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from start import run_database_migrations  # noqa: E402


def _normalize_database_url(value: str) -> str:
    value = value.strip()
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


def main() -> None:
    database_url = _normalize_database_url(os.environ.get("DATABASE_URL", ""))
    if not database_url:
        raise SystemExit("DATABASE_URL must contain the Neon PostgreSQL connection string")
    if not database_url.startswith("postgresql"):
        raise SystemExit("The hosted public portal requires PostgreSQL")
    app_secret = os.environ.get("APP_SECRET", "")
    if len(app_secret) < 32 or app_secret.startswith("replace-"):
        raise SystemExit("APP_SECRET must match the strong APP_SECRET used by local LeadFlow")

    env = os.environ.copy()
    env.update({
        "DATABASE_URL": database_url,
        "AUTO_CREATE_SCHEMA": "false",
        "AUTO_MIGRATE_DATABASE": "true",
        "SENDING_ENABLED": "false",
        "SCHEDULER_ENABLED": "false",
        "ASYNC_WORKERS_ENABLED": "false",
        "METRICS_ENABLED": "false",
        "DATABASE_POOL_SIZE": env.get("DATABASE_POOL_SIZE", "3"),
        "DATABASE_MAX_OVERFLOW": env.get("DATABASE_MAX_OVERFLOW", "2"),
    })
    if not env.get("PUBLIC_BASE_URL") and env.get("RENDER_EXTERNAL_URL"):
        env["PUBLIC_BASE_URL"] = env["RENDER_EXTERNAL_URL"]
    os.environ.update(env)

    run_database_migrations(env, use_venv=False)

    port = int(env.get("PORT", "10000"))
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.public_portal:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--workers",
        "1",
        "--no-access-log",
    ]
    os.chdir(ROOT)
    os.execve(sys.executable, command, env)


if __name__ == "__main__":
    main()
