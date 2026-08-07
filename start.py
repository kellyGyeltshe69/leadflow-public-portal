#!/usr/bin/env python3
"""One-command LeadFlow installer and launcher.

Local mode:
  * creates .env safely when missing
  * creates .venv
  * installs Python requirements when requirements.txt changes
  * automatically applies Alembic migrations when PostgreSQL is configured
  * starts/checks the selected Ollama or llama.cpp runtime when available
  * pulls/downloads the configured local model through that runtime
  * starts FastAPI/Uvicorn, which serves both the backend and Jinja frontend

Docker mode:
  * creates .env safely when missing
  * builds/starts the app and selected Ollama or llama.cpp runtime with Compose

This file intentionally uses only the Python standard library so it can run
before project dependencies are installed.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import venv
import webbrowser
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote as urlquote
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
REQUIREMENTS = ROOT / "requirements.txt"
VENV_DIR = ROOT / ".venv"
DATA_DIR = ROOT / "data"


def log(message: str) -> None:
    print(f"[LeadFlow] {message}", flush=True)


def clipboard_text() -> str:
    if os.name == "nt":
        try:
            return subprocess.check_output(
                ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    if sys.platform == "darwin" and shutil.which("pbpaste"):
        return subprocess.check_output(["pbpaste"], text=True).strip()
    for command in (["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]):
        if shutil.which(command[0]):
            return subprocess.check_output(command, text=True).strip()
    return ""


def set_clipboard(value: str) -> bool:
    try:
        if os.name == "nt":
            subprocess.run(["clip.exe"], input=value, text=True, check=True)
            return True
        if sys.platform == "darwin" and shutil.which("pbcopy"):
            subprocess.run(["pbcopy"], input=value, text=True, check=True)
            return True
        if shutil.which("xclip"):
            subprocess.run(["xclip", "-selection", "clipboard"], input=value, text=True, check=True)
            return True
        if shutil.which("xsel"):
            subprocess.run(["xsel", "--clipboard", "--input"], input=value, text=True, check=True)
            return True
    except (OSError, subprocess.CalledProcessError):
        return False
    return False


def open_or_print(url: str, no_browser: bool = False) -> None:
    print(f"  {url}")
    if not no_browser:
        with suppress(Exception):
            webbrowser.open(url)


def show_instruction_dialog(title: str, message: str) -> bool:
    """Use a modal Windows dialog so secrets are never typed into the terminal."""
    if os.name != "nt":
        return False
    root = None
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showinfo(title, message, parent=root)
        return True
    except Exception:
        return False
    finally:
        if root is not None:
            with suppress(Exception):
                root.destroy()


def complete_browser_step(title: str, message: str, fallback_phrase: str) -> None:
    if show_instruction_dialog(title, message):
        return
    _confirm_phrase(message + f" Type {fallback_phrase} here: ", fallback_phrase)


def set_env_line(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    replacement = f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)
    return text.rstrip() + "\n" + replacement + "\n"


PROJECT_SENDER_EMAIL = "david@leadflow.indevs.in"


def migrate_project_sender_email(text: str, values: dict[str, str], *, created: bool = False) -> str:
    """Move only this deployment's blank/legacy personal From identity to its business alias."""
    sender_email = values.get("SENDER_EMAIL", "").strip().lower()
    if created or sender_email in {"", "david@yourdomain.com"} or sender_email.endswith("@gmail.com"):
        # The private Gmail account remains the Composio login/forwarding inbox.
        # Sending still defaults off and Gmail must verify this alias before use.
        return set_env_line(text, "SENDER_EMAIL", PROJECT_SENDER_EMAIL)
    return text


def read_env(path: Path | None = None) -> dict[str, str]:
    path = path or ENV_FILE
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


INTEGER_ENV_DEFAULTS = {
    "DATABASE_POOL_SIZE": 10,
    "DATABASE_MAX_OVERFLOW": 20,
    "DATABASE_POOL_RECYCLE_SECONDS": 1800,
    "API_RATE_LIMIT_PER_MINUTE": 120,
    "AUTH_RATE_LIMIT_PER_MINUTE": 10,
    "JWT_ACCESS_MINUTES": 30,
    "DAILY_NEW_LEAD_LIMIT": 12,
    "DAILY_TOTAL_SEND_LIMIT": 50,
    "STARTUP_DISCOVERY_TARGET": 250,
    "STARTUP_DISCOVERY_COOLDOWN_HOURS": 24,
    "OLLAMA_NUM_CTX": 4096,
    "OLLAMA_TIMEOUT_SECONDS": 180,
    "LLAMACPP_NUM_CTX": 4096,
    "LLAMACPP_N_GPU_LAYERS": 0,
    "LLAMACPP_TIMEOUT_SECONDS": 300,
    "OPENAI_TIMEOUT_SECONDS": 60,
    "AUDIT_TIMEOUT_SECONDS": 15,
    "AUDIT_LINK_CHECK_LIMIT": 12,
    "AUDIT_LINK_WORKERS": 4,
    "BROWSER_AUDIT_TIMEOUT_SECONDS": 35,
    "REPLY_SYNC_MINUTES": 10,
    "SEND_QUEUE_MINUTES": 5,
    "SHEET_SYNC_MINUTES": 15,
    "DISCOVERY_CHECK_MINUTES": 30,
    "STARTUP_HEALTH_TIMEOUT_SECONDS": 120,
}


def repair_known_env_format_errors() -> dict[str, int]:
    """Repair only malformed non-secret integer values using documented defaults."""
    if not ENV_FILE.exists():
        return {}
    text = ENV_FILE.read_text(encoding="utf-8")
    values = read_env()
    repaired: dict[str, int] = {}
    for key, default in INTEGER_ENV_DEFAULTS.items():
        raw = values.get(key)
        if raw is None or raw == "":
            continue
        try:
            int(raw)
            continue
        except ValueError:
            pass
        numeric_prefix = re.fullmatch(r"\s*([0-9]+)(?:\.0+)?[A-Za-z]?\s*", raw)
        replacement = int(numeric_prefix.group(1)) if numeric_prefix else default
        text = set_env_line(text, key, str(replacement))
        repaired[key] = replacement
    if repaired:
        backup = ROOT / ".env.before-format-repair"
        if not backup.exists():
            shutil.copy2(ENV_FILE, backup)
        ENV_FILE.write_text(text, encoding="utf-8")
        with suppress(OSError):
            ENV_FILE.chmod(0o600)
        for key, value in repaired.items():
            log(f"Repaired malformed numeric setting {key}={value}.")
    return repaired


def _previous_env_candidates() -> list[Path]:
    search_root = ROOT.parent.parent
    candidates: list[Path] = []
    for pattern in (
        "leadflow_saas_production_v*/leadflow/.env",
        "leadflow_composio*/leadflow/.env",
    ):
        for candidate in search_root.glob(pattern):
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved == ENV_FILE.resolve() or candidate.is_symlink() or not candidate.is_file():
                continue
            with suppress(OSError):
                if candidate.stat().st_size <= 1_000_000:
                    candidates.append(candidate)
    return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)


def _database_url_reachable(database_url: str, *, use_venv: bool = True) -> bool:
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if not database_url.startswith("postgresql+"):
        return False
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    code = (
        "import os; from sqlalchemy import create_engine, text; "
        "e=create_engine(os.environ['DATABASE_URL'], pool_pre_ping=True); "
        "c=e.connect(); c.execute(text('SELECT 1')); c.close(); e.dispose()"
    )
    try:
        result = subprocess.run(
            [str(project_python(use_venv)), "-c", code],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=25,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def recover_previous_postgresql_env(*, use_venv: bool = True) -> Path | None:
    """Recover the newest reachable PostgreSQL .env from a sibling extracted release."""
    for candidate in _previous_env_candidates():
        values = read_env(candidate)
        database_url = values.get("DATABASE_URL", "")
        app_secret = values.get("APP_SECRET", "")
        if len(app_secret) < 32 or not database_url.startswith("postgresql"):
            continue
        if not _database_url_reachable(database_url, use_venv=use_venv):
            continue
        backup = ROOT / ".env.before-auto-recovery"
        if ENV_FILE.exists() and not backup.exists():
            shutil.copy2(ENV_FILE, backup)
        shutil.copy2(candidate, ENV_FILE)
        recovered_text = ENV_FILE.read_text(encoding="utf-8")
        migrated_text = migrate_project_sender_email(recovered_text, read_env())
        if migrated_text != recovered_text:
            ENV_FILE.write_text(migrated_text, encoding="utf-8")
        with suppress(OSError):
            ENV_FILE.chmod(0o600)
        log(f"Recovered the reachable PostgreSQL configuration from {candidate.parent}.")
        return candidate
    return None


def ensure_env_file() -> tuple[dict[str, str], str | None]:
    if not ENV_EXAMPLE.exists():
        raise SystemExit(".env.example is missing")
    created = not ENV_FILE.exists()
    text = ENV_EXAMPLE.read_text(encoding="utf-8") if created else ENV_FILE.read_text(encoding="utf-8")
    current = read_env() if not created else {}
    generated_password: str | None = None

    secret = current.get("APP_SECRET", "")
    if created or not secret or secret.startswith("replace-") or secret == "CHANGE-ME-BEFORE-PRODUCTION":
        text = set_env_line(text, "APP_SECRET", secrets.token_urlsafe(48))

    jwt_secret = current.get("JWT_SECRET", "")
    if created or not jwt_secret or jwt_secret.startswith("replace-") or jwt_secret == "CHANGE-ME-JWT-SECRET":
        text = set_env_line(text, "JWT_SECRET", secrets.token_urlsafe(48))

    encryption_key = current.get("ENCRYPTION_KEY", "")
    if created or not encryption_key or encryption_key.startswith("replace-"):
        text = set_env_line(text, "ENCRYPTION_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())

    metrics_token = current.get("METRICS_TOKEN", "")
    if created or not metrics_token or metrics_token.startswith("replace-"):
        text = set_env_line(text, "METRICS_TOKEN", secrets.token_urlsafe(32))

    postgres_password = current.get("POSTGRES_PASSWORD", "")
    if created or not postgres_password or postgres_password.startswith("replace-"):
        text = set_env_line(text, "POSTGRES_PASSWORD", secrets.token_urlsafe(32))

    grafana_password = current.get("GRAFANA_ADMIN_PASSWORD", "")
    if created or not grafana_password or grafana_password.startswith("replace-"):
        text = set_env_line(text, "GRAFANA_ADMIN_PASSWORD", secrets.token_urlsafe(24))

    password = current.get("ADMIN_PASSWORD", "")
    if created or not password or password.startswith("replace-") or password == "change-me":
        generated_password = secrets.token_urlsafe(18)
        text = set_env_line(text, "ADMIN_PASSWORD", generated_password)

    llama_key = current.get("LLAMACPP_API_KEY", "")
    if created or not llama_key or llama_key.startswith("replace-") or llama_key == "change-me-local":
        text = set_env_line(text, "LLAMACPP_API_KEY", secrets.token_urlsafe(32))

    text = migrate_project_sender_email(text, current, created=created)

    configured_database = current.get("DATABASE_URL", "").strip().lower()
    if configured_database.startswith("postgresql://") or configured_database.startswith("postgresql+"):
        # Alembic, not application startup, owns PostgreSQL schema changes.
        text = set_env_line(text, "AUTO_MIGRATE_DATABASE", current.get("AUTO_MIGRATE_DATABASE", "true") or "true")
        text = set_env_line(text, "AUTO_CREATE_SCHEMA", "false")

    ENV_FILE.write_text(text, encoding="utf-8")
    with suppress(OSError):
        ENV_FILE.chmod(0o600)
    if created:
        log("Created .env from .env.example with a random app secret and administrator password.")
    return read_env(), generated_password


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def requirements_fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(REQUIREMENTS.read_bytes())
    digest.update(f"{sys.version_info.major}.{sys.version_info.minor}".encode())
    return digest.hexdigest()


def install_python_requirements(force: bool = False) -> None:
    if not REQUIREMENTS.exists():
        raise SystemExit("requirements.txt is missing")
    python = venv_python()
    if not python.exists():
        log(f"Creating virtual environment at {VENV_DIR}")
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV_DIR)
    marker = VENV_DIR / ".leadflow-requirements.sha256"
    wanted = requirements_fingerprint()
    installed = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    if force or installed != wanted:
        log("Installing Python requirements. This can take a few minutes on first run.")
        subprocess.check_call([
            str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQUIREMENTS)
        ], cwd=ROOT)
        marker.write_text(wanted, encoding="utf-8")
    else:
        log("Python requirements are already installed and current.")


def project_python(use_venv: bool = True) -> Path:
    """Return the interpreter used for project modules and Uvicorn.

    start.py itself intentionally stays on the original interpreter. This avoids
    Windows venv redirector bugs in OneDrive paths containing spaces or
    parentheses. Child commands use `.venv\\Scripts\\python.exe -m ...`, the
    same reliable form pip already uses during installation.
    """
    candidate = venv_python()
    if use_venv and candidate.exists():
        # Preserve virtualenv interpreter symlinks so their site-packages remain active.
        return candidate.absolute()
    # Do not resolve the active interpreter symlink. Render and many Unix
    # platforms expose a virtualenv Python symlink whose resolved base binary
    # does not include the deployed virtualenv's site-packages.
    return Path(sys.executable).absolute()


def validate_project_configuration(
    file_values: dict[str, str],
    *,
    use_venv: bool = True,
) -> None:
    env = os.environ.copy()
    for key, value in file_values.items():
        env.setdefault(key, value)
    code = (
        "import json; from pydantic import ValidationError; from app.config import Settings; "
        "\ntry:\n Settings()\nexcept ValidationError as exc:\n"
        " print(json.dumps([{'field':'.'.join(str(x) for x in e['loc']),"
        "'message':e['msg']} for e in exc.errors()]))\n raise SystemExit(1)"
    )
    result = subprocess.run(
        [str(project_python(use_venv)), "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return
    detail = result.stdout.strip() or "unknown configuration validation error"
    raise SystemExit(
        "LeadFlow configuration is invalid and startup was stopped before database migration. "
        f"Fields: {detail}"
    )


def install_playwright_browser(use_venv: bool = True) -> None:
    marker = VENV_DIR / ".leadflow-playwright-chromium-ready"
    if marker.exists():
        log("Playwright Chromium is already installed for this virtual environment.")
        return
    log("Installing Playwright Chromium for optional mobile website research.")
    try:
        subprocess.check_call(
            [str(project_python(use_venv)), "-m", "playwright", "install", "chromium"],
            cwd=ROOT,
        )
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("installed\n", encoding="utf-8")
    except subprocess.CalledProcessError:
        log("Chromium installation failed. LeadFlow will continue with HTTP/HTML research only.")


def url_ready(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 500
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False


def wait_for_url(url: str, seconds: int) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if url_ready(url):
            return True
        time.sleep(1)
    return False


def wait_for_process_url(process: subprocess.Popen, url: str, seconds: int) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if url_ready(url):
            return True
        time.sleep(1)
    return False


def local_runtime_env(file_values: dict[str, str], host: str, port: int) -> dict[str, str]:
    env = os.environ.copy()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Shell variables win. The Docker-only /data SQLite path is translated for local mode.
    configured_database = env.get("DATABASE_URL") or file_values.get("DATABASE_URL", "")
    if not configured_database or configured_database == "sqlite:////data/leadflow.db":
        env["DATABASE_URL"] = f"sqlite:///{(DATA_DIR / 'leadflow.db').as_posix()}"
    else:
        env["DATABASE_URL"] = configured_database
    if is_postgresql_database(env):
        env["AUTO_CREATE_SCHEMA"] = "false"
    if file_values.get("DEMO_MODE", "true").lower() == "true" and "PUBLIC_BASE_URL" not in os.environ:
        public_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
        env["PUBLIC_BASE_URL"] = f"http://{public_host}:{port}"
    ollama_url = env.get("OLLAMA_BASE_URL") or file_values.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    parsed = urlparse(ollama_url)
    if parsed.hostname == "ollama":
        ollama_url = "http://127.0.0.1:11434"
    env["OLLAMA_BASE_URL"] = ollama_url
    screenshot_dir = env.get("BROWSER_SCREENSHOT_DIR") or file_values.get("BROWSER_SCREENSHOT_DIR", "")
    if not screenshot_dir or screenshot_dir == "/data/screenshots":
        env["BROWSER_SCREENSHOT_DIR"] = str((DATA_DIR / "screenshots").resolve())
    return env


def is_postgresql_database(env: dict[str, str]) -> bool:
    database_url = env.get("DATABASE_URL", "").strip().lower()
    return database_url.startswith("postgresql://") or database_url.startswith("postgresql+")


def run_database_migrations(
    env: dict[str, str],
    *,
    use_venv: bool = True,
    skip: bool = False,
) -> None:
    """Bring a configured local PostgreSQL database to Alembic head.

    Docker production keeps its dedicated `migrate` service. SQLite retains
    the compatibility initializer. This local launcher path prevents the web
    process from starting against an empty or outdated PostgreSQL schema.
    """
    if not is_postgresql_database(env):
        return
    enabled = env.get("AUTO_MIGRATE_DATABASE", "true").strip().lower() in {"1", "true", "yes", "on"}
    if skip or not enabled:
        log("Automatic PostgreSQL migrations are disabled; the configured schema must already be current.")
        return

    python = str(project_python(use_venv))
    log("PostgreSQL detected. Applying Alembic database migrations before startup.")
    try:
        subprocess.check_call(
            [python, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"],
            cwd=ROOT,
            env=env,
        )
        verification = subprocess.run(
            [
                python,
                "-c",
                (
                    "from sqlalchemy import inspect; "
                    "from app.db import engine; "
                    "raise SystemExit(0 if inspect(engine).has_table('users') else 1)"
                ),
            ],
            cwd=ROOT,
            env=env,
            check=False,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(
            "PostgreSQL migration failed, so LeadFlow was not started. "
            "Confirm the database, owner, password, and schema permissions, then run start.py again."
        ) from exc
    if verification.returncode != 0:
        raise SystemExit(
            "Alembic finished but the required users table is missing. "
            "LeadFlow was not started; inspect `alembic current` before retrying."
        )
    log("PostgreSQL schema is current and ready.")


def _is_neon_database(values: dict[str, str]) -> bool:
    try:
        host = (urlparse(values.get("DATABASE_URL", "")).hostname or "").lower()
    except ValueError:
        return False
    return host == "neon.tech" or host.endswith(".neon.tech")


def _prepare_public_portal_repository() -> Path:
    destination = DATA_DIR / "public-portal-deploy"
    destination.mkdir(parents=True, exist_ok=True)
    for child in destination.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache", ".mypy_cache")
    shutil.copytree(ROOT / "app", destination / "app", dirs_exist_ok=True, ignore=ignore)
    shutil.copytree(ROOT / "migrations", destination / "migrations", dirs_exist_ok=True, ignore=ignore)
    for relative in (
        "start.py",
        "alembic.ini",
        "render.yaml",
        "requirements-public.txt",
        ".gitignore",
        "THIRD_PARTY_NOTICES.md",
    ):
        shutil.copy2(ROOT / relative, destination / relative)
    scripts_dir = destination / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "scripts" / "start_public_portal.py", scripts_dir / "start_public_portal.py")
    shutil.copy2(ROOT / "docs" / "FREE_PUBLIC_PORTAL.md", destination / "README.md")

    forbidden = [
        path
        for path in destination.rglob("*")
        if path.is_file()
        and (
            path.name == ".env"
            or path.name.startswith(".env.before")
            or path.suffix in {".db", ".sqlite", ".dump"}
        )
    ]
    if forbidden:
        raise SystemExit("Refusing to prepare a portal repository containing local secrets or database files")
    return destination


def _git_capture(git: str, repository: Path, *arguments: str) -> str:
    return subprocess.check_output(
        [git, *arguments],
        cwd=repository,
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


def _extract_postgresql_url(value: str) -> str:
    match = re.search(r"postgresql(?:\+psycopg)?://[^\s'\"<>]+", value or "", re.IGNORECASE)
    return match.group(0).rstrip(");,`") if match else ""


def _validated_direct_neon_url(value: str) -> tuple[str, str]:
    candidate = _extract_postgresql_url(value)
    if not candidate:
        return "", "Clipboard does not contain a PostgreSQL connection string"
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return "", "The copied PostgreSQL connection string is malformed"
    host = (parsed.hostname or "").lower()
    if not (host == "neon.tech" or host.endswith(".neon.tech")):
        return "", "The connection string is not for a Neon database"
    if "-pooler." in host:
        return "", "Connection pooling is on; copy the Direct connection string"
    if not parsed.username or not parsed.password or not parsed.path.strip("/"):
        return "", "The Neon connection string is missing its user, password, or database"
    if candidate.startswith("postgresql://"):
        candidate = candidate.replace("postgresql://", "postgresql+psycopg://", 1)
    return candidate, ""


def _valid_github_repository(value: str) -> str:
    value = value.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        return ""
    return value


def _valid_public_portal_url(value: str) -> str:
    value = value.strip().rstrip("/")
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    blocked_hosts = {"dashboard.render.com", "render.com", "console.neon.tech", "github.com"}
    if (
        parsed.scheme != "https"
        or not host
        or host in blocked_hosts
        or host.endswith(".github.com")
        or "yourdomain" in host
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return ""
    return value


def _is_render_dashboard_url(value: str) -> bool:
    try:
        return (urlparse(value.strip()).hostname or "").lower() == "dashboard.render.com"
    except ValueError:
        return False


def _read_public_portal_url_from_clipboard() -> str:
    while True:
        message = (
            "Wait until Render shows the web service as Live.\n\n"
            "Copy the public service URL shown near the service name "
            "(for example, https://NAME.onrender.com).\n\n"
            "Do not copy the dashboard.render.com browser address. "
            "Return here and click OK after copying the public URL."
        )
        if not show_instruction_dialog("LeadFlow: copy the public Render URL", message):
            input(message + " Then press Enter here: ")
        candidate = _valid_public_portal_url(clipboard_text())
        if candidate:
            return candidate
        print("The clipboard does not contain a public service origin such as https://NAME.onrender.com.")
        print("Do not copy a dashboard.render.com browser address.")


def _save_env_value(key: str, value: str) -> None:
    text = ENV_FILE.read_text(encoding="utf-8")
    ENV_FILE.write_text(set_env_line(text, key, value), encoding="utf-8")


def _saved_portal_metadata(values: dict[str, str]) -> tuple[str, str]:
    return (
        _valid_public_portal_url(values.get("PUBLIC_REPORT_BASE_URL", "")),
        _valid_github_repository(values.get("PUBLIC_PORTAL_GITHUB_REPOSITORY", "")),
    )


def public_portal_setup_status(values: dict[str, str]) -> tuple[str, str]:
    raw_report_url = values.get("PUBLIC_REPORT_BASE_URL", "")
    valid_report_url = _valid_public_portal_url(raw_report_url)
    github_url = _valid_github_repository(
        values.get("PUBLIC_PORTAL_GITHUB_REPOSITORY", "")
    )
    render_exists = values.get("PUBLIC_PORTAL_RENDER_SERVICE_EXISTS", "false").lower() == "true"
    neon_configured = _is_neon_database(values)
    requested = bool(raw_report_url or github_url or render_exists or neon_configured)
    if not requested:
        return "not_requested", ""
    if valid_report_url:
        return "complete", ""
    if _is_render_dashboard_url(raw_report_url):
        return (
            "incomplete",
            "Render exists, but PUBLIC_REPORT_BASE_URL is a dashboard URL. "
            "Run: python start.py --repair-render-portal",
        )
    if render_exists:
        return (
            "incomplete",
            "Render exists, but its public service URL is missing. "
            "Run: python start.py --repair-render-portal",
        )
    if github_url:
        return (
            "incomplete",
            "GitHub is ready, but Render setup is incomplete. "
            "Run: python start.py --setup-free-portal",
        )
    return (
        "incomplete",
        "Neon is configured, but the public portal is incomplete. "
        "Run: python start.py --setup-free-portal",
    )


def _public_portal_health(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=20) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return payload.get("status") == "ok"
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return False


def _wait_for_public_portal(url: str, seconds: int) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _public_portal_health(url):
            return True
        remaining = max(0, int(deadline - time.monotonic()))
        print(f"[LeadFlow] Waiting for Render portal health ({remaining}s remaining)...", flush=True)
        time.sleep(min(10, max(1, remaining)))
    return False


def _confirm_phrase(prompt: str, expected: str) -> None:
    while True:
        response = input(prompt).strip()
        if response.upper() == expected:
            return
        looks_sensitive = bool(_extract_postgresql_url(response)) or (
            len(response) >= 24 and " " not in response
        )
        if looks_sensitive:
            set_clipboard("")
            raise SystemExit(
                "A credential was entered into the confirmation prompt instead of the Render browser field. "
                "Stop and rotate the exposed portal credentials before continuing."
            )
        print(f"Type {expected} exactly after completing the browser step.")


def _render_secret_handoff(values: dict[str, str]) -> None:
    database_url = values.get("DATABASE_URL", "")
    app_secret = values.get("APP_SECRET", "")
    if not database_url or len(app_secret) < 32:
        raise SystemExit("DATABASE_URL or APP_SECRET is missing; Render setup stopped")

    if not set_clipboard(database_url):
        raise SystemExit("Could not copy DATABASE_URL to the system clipboard")
    complete_browser_step(
        "LeadFlow: paste DATABASE_URL into Render",
        "DATABASE_URL is now on the clipboard.\n\n"
        "1. Switch to Render's Environment page.\n"
        "2. Click the DATABASE_URL VALUE field.\n"
        "3. Press Ctrl+A, then Ctrl+V.\n"
        "4. Return to this dialog and click OK.\n\n"
        "Do not paste the value into PowerShell or chat.",
        "DATABASE PASTED",
    )
    if not set_clipboard(app_secret):
        raise SystemExit("Could not copy APP_SECRET to the system clipboard")
    complete_browser_step(
        "LeadFlow: paste APP_SECRET into Render",
        "APP_SECRET is now on the clipboard.\n\n"
        "1. Switch to Render's Environment page.\n"
        "2. Click the APP_SECRET VALUE field.\n"
        "3. Press Ctrl+A, then Ctrl+V.\n"
        "4. Return to this dialog and click OK.\n\n"
        "Do not paste the value into PowerShell or chat.",
        "APP PASTED",
    )
    set_clipboard("")


def _push_portal_repository(git: str, repository: Path) -> None:
    """Update a dedicated remote branch while preserving its existing history."""
    remote_has_main = subprocess.run(
        [git, "ls-remote", "--exit-code", "--heads", "origin", "main"],
        cwd=repository,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0
    try:
        if remote_has_main:
            log("Existing GitHub main branch detected; preserving its history and applying the portal update.")
            subprocess.check_call([git, "fetch", "origin", "main"], cwd=repository)
            subprocess.check_call([git, "reset", "--soft", "origin/main"], cwd=repository)
            if _git_capture(git, repository, "status", "--porcelain"):
                subprocess.check_call(
                    [git, "commit", "-m", "Update LeadFlow public report portal"],
                    cwd=repository,
                )
        subprocess.check_call([git, "push", "--set-upstream", "origin", "main"], cwd=repository)
    except subprocess.CalledProcessError:
        raise SystemExit(
            "GitHub synchronization failed. The existing remote was not deleted or force-pushed. "
            "Confirm GitHub browser authorization, then rerun start.py --setup-free-portal."
        ) from None


def _setup_github_repository(args: argparse.Namespace, saved_url: str = "") -> str:
    print("\nStep 2/4: Prepare and publish a private GitHub deployment repository.")
    repository = _prepare_public_portal_repository()
    git = shutil.which("git")
    if not git:
        open_or_print("https://git-scm.com/download/win", args.no_browser)
        raise SystemExit("Install Git for Windows, then rerun the same start.py --setup-free-portal command")
    if not (repository / ".git").exists():
        try:
            subprocess.check_call([git, "init", "-b", "main"], cwd=repository)
        except subprocess.CalledProcessError:
            subprocess.check_call([git, "init"], cwd=repository)
            subprocess.check_call([git, "branch", "-M", "main"], cwd=repository)
    subprocess.check_call([git, "config", "user.name", "LeadFlow Deployment"], cwd=repository)
    subprocess.check_call([git, "config", "core.autocrlf", "false"], cwd=repository)
    subprocess.check_call(
        [git, "config", "user.email", "leadflow-deploy@users.noreply.github.com"],
        cwd=repository,
    )
    subprocess.check_call([git, "add", "--all"], cwd=repository)
    if _git_capture(git, repository, "status", "--porcelain"):
        subprocess.check_call(
            [git, "commit", "-m", "Deploy LeadFlow public report portal"],
            cwd=repository,
        )

    repository_url = _valid_github_repository(saved_url)
    if not repository_url:
        try:
            repository_url = _valid_github_repository(
                _git_capture(git, repository, "remote", "get-url", "origin")
            )
        except subprocess.CalledProcessError:
            repository_url = ""
    if not repository_url:
        print("GitHub repository status:")
        print("  1. It already exists")
        print("  2. Create a new private repository")
        choice = input("Choose 1 or 2 [1]: ").strip() or "1"
        if choice == "1":
            open_or_print("https://github.com/", args.no_browser)
            prompt = "Open the existing private repository, copy its HTTPS URL, then press Enter here: "
        else:
            open_or_print(
                "https://github.com/new?name=leadflow-public-portal&visibility=private",
                args.no_browser,
            )
            prompt = "Create it as PRIVATE, copy its HTTPS URL, then press Enter here: "
        while not repository_url:
            input(prompt)
            repository_url = _valid_github_repository(clipboard_text())
            if not repository_url:
                print("The clipboard does not contain a valid https://github.com/OWNER/REPOSITORY URL.")

    subprocess.run([git, "remote", "remove", "origin"], cwd=repository, check=False)
    subprocess.check_call([git, "remote", "add", "origin", repository_url + ".git"], cwd=repository)
    _save_env_value("PUBLIC_PORTAL_GITHUB_REPOSITORY", repository_url)
    print("Git may open a browser for GitHub authorization. Complete that official login if prompted.")
    _push_portal_repository(git, repository)
    return repository_url


def setup_free_public_portal(args: argparse.Namespace) -> dict[str, str]:
    """Interactive one-command setup; account consent remains in official browser pages."""
    log("Starting the free Render + Neon public portal setup wizard.")
    print("This wizard does not ask you to paste secrets into PowerShell or chat.")
    print("You will authorize external accounts in their official browser pages and use the clipboard when prompted.\n")

    values = read_env()
    if not values.get("DATABASE_URL", "").startswith("postgresql"):
        recovered = recover_previous_postgresql_env(use_venv=not args.no_install)
        if recovered:
            values = read_env()
    if not _is_neon_database(values):
        if not values.get("DATABASE_URL", "").startswith("postgresql"):
            raise SystemExit(
                "No reachable PostgreSQL configuration was found in this or a previous extracted LeadFlow folder. "
                "Keep the previous folder in Downloads and rerun this wizard so start.py can recover its .env."
            )
        print("Step 1/4: Create a free Neon project and copy its DIRECT connection string.")
        open_or_print("https://console.neon.tech/app/projects", args.no_browser)
        python = str(project_python(use_venv=not args.no_install))
        try:
            subprocess.check_call(
                [python, str(ROOT / "scripts" / "migrate_to_neon.py")],
                cwd=ROOT,
                env=os.environ.copy(),
            )
        except subprocess.CalledProcessError:
            raise SystemExit(
                "Neon migration did not complete. No local data was changed. "
                "Correct the Neon connection selection and rerun start.py --setup-free-portal."
            ) from None
        values = read_env()
        if not _is_neon_database(values):
            raise SystemExit("Neon migration did not update DATABASE_URL; portal setup stopped")
    else:
        log("Neon DATABASE_URL is already configured; data migration is not repeated.")

    values = read_env()
    raw_report_url = values.get("PUBLIC_REPORT_BASE_URL", "")
    portal_url, repository_url = _saved_portal_metadata(values)
    portal_is_configured = bool(portal_url)
    render_service_exists = (
        portal_is_configured
        or _is_render_dashboard_url(raw_report_url)
        or values.get("PUBLIC_PORTAL_RENDER_SERVICE_EXISTS", "false").lower() == "true"
    )
    if render_service_exists:
        print("\nStep 2/4: GitHub repository setup is already complete; skipping it.")
        if repository_url:
            print(f"  {repository_url}")
    else:
        repository_url = _setup_github_repository(args, repository_url)
        values = read_env()

    portal_is_healthy = portal_is_configured and _wait_for_public_portal(portal_url, 60)
    if portal_is_configured and portal_is_healthy:
        print("\nStep 3/4: The existing healthy Render portal will auto-deploy the pushed update.")
        print(f"  {portal_url}")
    elif render_service_exists:
        print("\nStep 3/4: Repair the existing Render portal environment.")
        if portal_url:
            print(f"  Existing portal is not healthy: {portal_url}")
        else:
            print("  The saved URL is a Render dashboard address, not the public service URL.")
        open_or_print("https://dashboard.render.com/", args.no_browser)
        complete_browser_step(
            "LeadFlow: open the existing Render service",
            "In Render, open leadflow-report-portal, select Environment, and click Edit.\n\n"
            "Return to this dialog and click OK when the value fields are editable.",
            "ENVIRONMENT READY",
        )
        _render_secret_handoff(values)
        complete_browser_step(
            "LeadFlow: redeploy the portal",
            "In Render, click Save Changes / Save and Deploy.\n\n"
            "Return to this dialog and click OK after deployment starts.",
            "DEPLOY STARTED",
        )
        if not portal_url:
            print("\nStep 4/4: Save the actual public web-service URL.")
            portal_url = _read_public_portal_url_from_clipboard()
    else:
        print("\nStep 3/4: Deploy the Render Blueprint.")
        blueprint_url = "https://dashboard.render.com/blueprint/new?repo=" + urlquote(
            repository_url,
            safe="",
        )
        open_or_print(blueprint_url, args.no_browser)
        _render_secret_handoff(values)
        complete_browser_step(
            "LeadFlow: deploy the Render Blueprint",
            "In Render, click Deploy Blueprint.\n\n"
            "Return to this dialog and click OK after deployment starts.",
            "DEPLOY STARTED",
        )

        print("\nStep 4/4: Connect local report links to the deployed portal.")
        portal_url = _read_public_portal_url_from_clipboard()

    if portal_url and portal_url != _valid_public_portal_url(raw_report_url):
        backup = ROOT / ".env.before-public-portal"
        if not backup.exists():
            shutil.copy2(ENV_FILE, backup)
        _save_env_value("PUBLIC_REPORT_BASE_URL", portal_url)
        _save_env_value("PUBLIC_PORTAL_RENDER_SERVICE_EXISTS", "true")
        _save_env_value("SENDING_ENABLED", "false")
        set_clipboard("")

    if not portal_is_healthy:
        log("Waiting for the public portal health check. Free services can take several minutes to deploy.")
        portal_is_healthy = _wait_for_public_portal(portal_url, 600)
    if not portal_is_healthy:
        print("The portal URL is saved, but its health check is still unavailable.")
        print("Review the Render deployment logs, then rerun this wizard; it will enter repair mode again.")
    else:
        log("Public portal is online and connected to Neon.")
        open_or_print(portal_url, args.no_browser)
    print("SENDING_ENABLED remains false. Test report, click, and opt-out routes before enabling it.\n")
    return read_env()


def rotate_public_portal_credentials(args: argparse.Namespace) -> dict[str, str]:
    """Rotate credentials after accidental disclosure, without shell-pasting them."""
    log("Starting emergency Neon password and public-portal secret rotation.")
    values = read_env()
    previous_database_url = values.get("DATABASE_URL", "")
    if not _is_neon_database(values):
        raise SystemExit("Credential rotation requires an existing Neon DATABASE_URL")

    print("In Neon, open the project, click Connect, choose the current role, and click Reset password.")
    print("Then turn Connection pooling OFF and copy the new Direct connection string.")
    open_or_print("https://console.neon.tech/app/projects", args.no_browser)
    new_database_url = ""
    while not new_database_url:
        message = (
            "In Neon, reset the current role password, turn Connection pooling OFF, and copy the NEW "
            "Direct connection string.\n\nReturn to this dialog and click OK after copying it."
        )
        if not show_instruction_dialog("LeadFlow: copy the rotated Neon URL", message):
            input(message + " Then press Enter here: ")
        candidate, error = _validated_direct_neon_url(clipboard_text())
        if not candidate:
            print(f"Not ready: {error}")
            continue
        if candidate == previous_database_url:
            print("The copied URL is unchanged. Reset the Neon role password first, then copy the new URL.")
            continue
        new_database_url = candidate

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = ROOT / f".env.before-credential-rotation-{stamp}"
    shutil.copy2(ENV_FILE, backup)
    new_app_secret = secrets.token_urlsafe(48)
    env_text = ENV_FILE.read_text(encoding="utf-8")
    env_text = set_env_line(env_text, "DATABASE_URL", new_database_url)
    env_text = set_env_line(env_text, "APP_SECRET", new_app_secret)
    env_text = set_env_line(env_text, "SENDING_ENABLED", "false")
    ENV_FILE.write_text(env_text, encoding="utf-8")
    with suppress(OSError):
        ENV_FILE.chmod(0o600)
    set_clipboard("")

    print("The local Neon URL and APP_SECRET have been rotated. Existing signed report links are now invalid.")
    print("Now update the existing Render service with the new values.")
    open_or_print("https://dashboard.render.com/", args.no_browser)
    complete_browser_step(
        "LeadFlow: open the existing Render service",
        "In Render, open leadflow-report-portal → Environment → Edit.\n\n"
        "Return to this dialog and click OK when the fields are editable.",
        "ENVIRONMENT READY",
    )
    rotated_values = read_env()
    _render_secret_handoff(rotated_values)
    complete_browser_step(
        "LeadFlow: deploy rotated credentials",
        "In Render, click Save Changes / Save and Deploy.\n\n"
        "Return to this dialog and click OK after deployment starts.",
        "DEPLOY STARTED",
    )

    raw_portal_url = rotated_values.get("PUBLIC_REPORT_BASE_URL", "")
    portal_url = _valid_public_portal_url(raw_portal_url)
    if not portal_url:
        print("Copy the actual public service URL after Render shows the service as Live.")
        portal_url = _read_public_portal_url_from_clipboard()
        _save_env_value("PUBLIC_REPORT_BASE_URL", portal_url)
    _save_env_value("PUBLIC_PORTAL_RENDER_SERVICE_EXISTS", "true")
    set_clipboard("")

    log("Waiting for the rotated Render portal to become healthy.")
    if not _wait_for_public_portal(portal_url, 600):
        print("Credential rotation was saved, but Render is not healthy yet. Review its deploy logs.")
    else:
        log("Credential rotation completed and the public portal is healthy.")
        open_or_print(portal_url, args.no_browser)
    print(f"Previous local settings were backed up as {backup.name}. Keep that file private.")
    return read_env()


def update_public_portal_source(args: argparse.Namespace) -> dict[str, str]:
    """Push the current sanitized portal source to the existing private repository."""
    log("Updating the existing public-portal source repository (no secrets are changed).")
    values = read_env()
    if not values.get("DATABASE_URL", "").startswith("postgresql"):
        recovered = recover_previous_postgresql_env(use_venv=not args.no_install)
        if recovered:
            values = read_env()
    repository_url = _valid_github_repository(
        values.get("PUBLIC_PORTAL_GITHUB_REPOSITORY", "")
    )
    repository_url = _setup_github_repository(args, repository_url)
    _save_env_value("PUBLIC_PORTAL_GITHUB_REPOSITORY", repository_url)
    open_or_print("https://dashboard.render.com/", args.no_browser)
    portal_url = _valid_public_portal_url(values.get("PUBLIC_REPORT_BASE_URL", ""))
    if portal_url:
        log("Waiting for Render's automatic deploy of the updated portal source.")
        if _wait_for_public_portal(portal_url, 600):
            log("Updated public portal is healthy.")
            open_or_print(portal_url, args.no_browser)
        else:
            print("The source was pushed, but the portal is not healthy yet. Check Render's latest deploy logs.")
    else:
        print("Source update pushed. Open the existing leadflow-report-portal service and monitor its deploy.")
    return read_env()


def repair_existing_render_portal(args: argparse.Namespace) -> dict[str, str]:
    """Repair only the existing Render service; never touch Neon or GitHub state."""
    log("Starting existing Render portal repair (Neon and GitHub are skipped).")
    values = read_env()
    if not values.get("DATABASE_URL", "").startswith("postgresql"):
        recovered = recover_previous_postgresql_env(use_venv=not args.no_install)
        if recovered:
            values = read_env()
    if not _is_neon_database(values):
        raise SystemExit(
            "No reachable Neon configuration was found. Keep the previous LeadFlow folders in Downloads "
            "and rerun --repair-render-portal."
        )
    if len(values.get("APP_SECRET", "")) < 32:
        raise SystemExit("APP_SECRET is missing or too short; Render repair stopped")

    open_or_print("https://dashboard.render.com/", args.no_browser)
    complete_browser_step(
        "LeadFlow: open the existing Render service",
        "In Render, open the EXISTING leadflow-report-portal service.\n"
        "Select Environment and click Edit.\n\n"
        "Do not create another Blueprint or service. Return here and click OK when the fields are editable.",
        "ENVIRONMENT READY",
    )
    _render_secret_handoff(values)
    complete_browser_step(
        "LeadFlow: redeploy the existing portal",
        "In Render, click Save Changes / Save and Deploy on the EXISTING service.\n\n"
        "Return here and click OK after deployment starts.",
        "DEPLOY STARTED",
    )

    raw_portal_url = values.get("PUBLIC_REPORT_BASE_URL", "")
    portal_url = _valid_public_portal_url(raw_portal_url)
    if not portal_url:
        portal_url = _read_public_portal_url_from_clipboard()
    backup = ROOT / ".env.before-render-repair"
    if not backup.exists():
        shutil.copy2(ENV_FILE, backup)
    _save_env_value("PUBLIC_REPORT_BASE_URL", portal_url)
    _save_env_value("PUBLIC_PORTAL_RENDER_SERVICE_EXISTS", "true")
    _save_env_value("SENDING_ENABLED", "false")
    set_clipboard("")

    log("Waiting for the existing Render portal to become healthy.")
    if not _wait_for_public_portal(portal_url, 600):
        print("Render is still unhealthy. No new service was created; review the existing service's deploy logs.")
    else:
        log("Existing Render portal is healthy and connected to Neon.")
        open_or_print(portal_url, args.no_browser)
    return read_env()


def maybe_start_ollama(env: dict[str, str], skip: bool, skip_pull: bool) -> subprocess.Popen | None:
    if skip or env.get("AI_PROVIDER", read_env().get("AI_PROVIDER", "ollama")).lower() != "ollama":
        return None
    base_url = env.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    parsed = urlparse(base_url)
    local_host = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if url_ready(base_url + "/api/tags"):
        log(f"Ollama is already reachable at {base_url}")
        process = None
    elif not local_host:
        log(f"Ollama endpoint {base_url} is not reachable; LeadFlow will use deterministic fallback drafts.")
        return None
    else:
        binary = shutil.which("ollama")
        if not binary:
            log("Ollama is not installed locally. LeadFlow can still start with deterministic fallback drafts.")
            log("For automatic Ollama setup, use: python start.py --docker")
            return None
        ollama_env = env.copy()
        ollama_env["OLLAMA_HOST"] = f"{parsed.hostname or '127.0.0.1'}:{parsed.port or 11434}"
        log("Starting local Ollama server.")
        process = subprocess.Popen([binary, "serve"], cwd=ROOT, env=ollama_env)
        if not wait_for_url(base_url + "/api/tags", 30):
            process.terminate()
            raise SystemExit("Ollama did not become ready within 30 seconds")
    if not skip_pull:
        binary = shutil.which("ollama")
        if binary:
            model = env.get("OLLAMA_MODEL") or read_env().get("OLLAMA_MODEL", "qwen3:4b-instruct")
            pull_env = env.copy()
            pull_env["OLLAMA_HOST"] = base_url
            log(f"Ensuring Ollama model is installed: {model}")
            try:
                subprocess.check_call([binary, "pull", model], cwd=ROOT, env=pull_env)
            except subprocess.CalledProcessError:
                log("Model pull failed; deterministic fallback drafting remains available.")
    return process


def maybe_start_llamacpp(env: dict[str, str], skip: bool) -> subprocess.Popen | None:
    if skip:
        return None
    file_values = read_env()
    base_url = env.get("LLAMACPP_BASE_URL") or file_values.get("LLAMACPP_BASE_URL", "http://127.0.0.1:8080")
    parsed = urlparse(base_url)
    if parsed.hostname == "llama-cpp":
        base_url = "http://127.0.0.1:8080"
        parsed = urlparse(base_url)
    env["LLAMACPP_BASE_URL"] = base_url
    if url_ready(base_url.rstrip("/") + "/health"):
        log(f"llama.cpp is already reachable at {base_url}")
        return None
    local_host = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not local_host:
        log(f"llama.cpp endpoint {base_url} is not reachable; deterministic fallback drafts remain available.")
        return None
    server_binary = shutil.which("llama-server")
    llama_binary = shutil.which("llama") if not server_binary else None
    if not server_binary and not llama_binary:
        log("llama.cpp is not installed locally. Use --docker --ai-runtime llamacpp for automatic runtime setup.")
        return None
    model = env.get("LLAMACPP_HF_MODEL") or file_values.get("LLAMACPP_HF_MODEL", "ggml-org/Qwen3-4B-GGUF:Q4_K_M")
    alias = env.get("LLAMACPP_MODEL_ALIAS") or file_values.get("LLAMACPP_MODEL_ALIAS", "leadflow-local")
    api_key = env.get("LLAMACPP_API_KEY") or file_values.get("LLAMACPP_API_KEY", "change-me-local")
    context = env.get("LLAMACPP_NUM_CTX") or file_values.get("LLAMACPP_NUM_CTX", "4096")
    gpu_layers = env.get("LLAMACPP_N_GPU_LAYERS") or file_values.get("LLAMACPP_N_GPU_LAYERS", "0")
    host = parsed.hostname or "127.0.0.1"
    port = str(parsed.port or 8080)
    command = [server_binary] if server_binary else [str(llama_binary), "serve"]
    command += [
        "-hf", model,
        "--alias", alias,
        "--ctx-size", context,
        "--n-gpu-layers", gpu_layers,
        "--parallel", "1",
        "--jinja",
        "--api-key", api_key,
        "--host", host,
        "--port", port,
    ]
    log(f"Starting llama.cpp with {model}; the first model download can take several minutes.")
    process = subprocess.Popen(command, cwd=ROOT, env=env)
    if not wait_for_url(base_url.rstrip("/") + "/health", 600):
        terminate(process, "llama.cpp")
        log("llama.cpp did not become ready; LeadFlow can still use deterministic fallback drafts.")
        return None
    return process


def terminate(process: subprocess.Popen | None, name: str) -> None:
    if not process or process.poll() is not None:
        return
    log(f"Stopping {name}.")
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_preflight(args: argparse.Namespace, file_values: dict[str, str]) -> int:
    checks: list[tuple[str, str, str]] = []
    runtime_mode = "demo" if file_values.get("DEMO_MODE", "true").lower() == "true" else "live"

    def add(level: str, name: str, detail: str) -> None:
        checks.append((level, name, detail))

    add("PASS" if sys.version_info >= (3, 11) else "FAIL", "Python", sys.version.split()[0])
    for required in [REQUIREMENTS, ENV_FILE, ROOT / "app" / "main.py"]:
        add("PASS" if required.exists() else "FAIL", required.name, str(required))

    secret = file_values.get("APP_SECRET", "")
    jwt_secret = file_values.get("JWT_SECRET", "")
    encryption_key = file_values.get("ENCRYPTION_KEY", "")
    password = file_values.get("ADMIN_PASSWORD", "")
    add("PASS" if len(secret) >= 32 and not secret.startswith("replace-") else "FAIL", "APP_SECRET", "strong" if len(secret) >= 32 else "missing/short")
    add("PASS" if len(jwt_secret) >= 32 and not jwt_secret.startswith("replace-") else "FAIL", "JWT_SECRET", "strong" if len(jwt_secret) >= 32 else "missing/short")
    add("PASS" if len(encryption_key) >= 32 and not encryption_key.startswith("replace-") else "FAIL", "ENCRYPTION_KEY", "configured" if len(encryption_key) >= 32 else "missing/short")
    add("PASS" if len(password) >= 16 and not password.startswith("replace-") else "FAIL", "ADMIN_PASSWORD", "strong" if len(password) >= 16 else "missing/short")

    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = DATA_DIR / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("PASS", "Local data directory", str(DATA_DIR))
    except OSError as exc:
        add("FAIL", "Local data directory", str(exc))

    try:
        with socket.socket() as probe_socket:
            probe_socket.bind((args.host, args.port))
        add("PASS", "Dashboard port", f"{args.host}:{args.port} is available")
    except OSError as exc:
        add("FAIL", "Dashboard port", str(exc))

    if not args.docker:
        python = str(project_python(use_venv=not args.no_install))
        for module in ["fastapi", "sqlalchemy", "composio", "httpx"]:
            completed = subprocess.run(
                [python, "-c", f"import {module}"],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            installed = completed.returncode == 0
            add("PASS" if installed else "FAIL", f"Python module {module}", "installed" if installed else "missing")
        try:
            mode_output = subprocess.check_output(
                [python, "-m", "app.cli", "mode-status"],
                cwd=ROOT,
                env=local_runtime_env(file_values, args.host, args.port),
                text=True,
                timeout=30,
            )
            runtime_mode = json.loads(mode_output)["runtime_mode"]
        except (subprocess.SubprocessError, ValueError, KeyError, json.JSONDecodeError):
            add("WARN", "Runtime mode", "could not read persisted mode; using .env default")
    add("PASS" if runtime_mode == "live" else "WARN", "Runtime mode", runtime_mode)

    runtime = args.ai_runtime
    if args.docker:
        docker_ok = bool(shutil.which("docker"))
        add("PASS" if docker_ok else "FAIL", "Docker", "available" if docker_ok else "not found")
        compose_files = [Path(item) for item in docker_command(args) if str(item).endswith((".yml", ".yaml"))]
        missing = [str(path) for path in compose_files if not (ROOT / path).exists()]
        add("PASS" if not missing else "FAIL", "Compose files", "all present" if not missing else f"missing: {', '.join(missing)}")
    elif runtime == "ollama":
        env = local_runtime_env(file_values, args.host, args.port)
        endpoint = env.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        ready = url_ready(endpoint + "/api/tags")
        binary = shutil.which("ollama")
        level = "PASS" if ready or binary else "WARN"
        add(level, "Ollama", f"reachable at {endpoint}" if ready else f"binary: {binary or 'not found; deterministic fallback will be used'}")
    elif runtime == "llamacpp":
        env = local_runtime_env(file_values, args.host, args.port)
        endpoint = env.get("LLAMACPP_BASE_URL", "http://127.0.0.1:8080").replace("llama-cpp", "127.0.0.1").rstrip("/")
        ready = url_ready(endpoint + "/health")
        binary = shutil.which("llama-server") or shutil.which("llama")
        level = "PASS" if ready or binary else "WARN"
        add(level, "llama.cpp", f"reachable at {endpoint}" if ready else f"binary: {binary or 'not found; deterministic fallback will be used'}")
    else:
        endpoint = file_values.get("OPENAI_BASE_URL", "").strip()
        api_key = file_values.get("OPENAI_API_KEY", "").strip()
        model = file_values.get("OPENAI_MODEL", "").strip()
        configured = bool(
            endpoint.startswith("https://")
            and api_key
            and not api_key.startswith("replace-")
            and model
        )
        add(
            "PASS" if configured else "FAIL",
            "Hosted AI provider",
            f"configured model: {model}" if configured else "OPENAI_BASE_URL/API_KEY/MODEL incomplete",
        )

    if args.browser_audit and not args.docker:
        python = str(project_python(use_venv=not args.no_install))
        playwright_installed = subprocess.run(
            [python, "-c", "import playwright"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode == 0
        add("PASS" if playwright_installed else "FAIL", "Playwright package", "installed" if playwright_installed else "missing")

    demo_mode = runtime_mode == "demo"
    if demo_mode:
        add("WARN", "Live mode", "DEMO_MODE=true; no live Google discovery or email sending")
    else:
        required_live = {
            "COMPOSIO_API_KEY": file_values.get("COMPOSIO_API_KEY", ""),
            "COMPOSIO_USER_ID": file_values.get("COMPOSIO_USER_ID", ""),
            "SENDER_EMAIL": file_values.get("SENDER_EMAIL", ""),
            "PHYSICAL_POSTAL_ADDRESS": file_values.get("PHYSICAL_POSTAL_ADDRESS", ""),
        }
        for key, value in required_live.items():
            add("PASS" if value else "FAIL", key, "configured" if value else "missing")
        postal_attested = file_values.get("POSTAL_ADDRESS_ATTESTED", "false").lower() == "true"
        add(
            "PASS" if postal_attested else "FAIL",
            "Postal address attestation",
            "confirmed" if postal_attested else "required before approval/sending",
        )
        public_url = file_values.get("PUBLIC_REPORT_BASE_URL", "") or file_values.get("PUBLIC_BASE_URL", "")
        public_ok = public_url.startswith("https://") and "yourdomain" not in public_url
        add("PASS" if public_ok else "FAIL", "Public report URL", public_url or "missing")
        sending = file_values.get("SENDING_ENABLED", "false").lower() == "true"
        add("PASS" if sending else "WARN", "Sending", "enabled" if sending else "disabled (safe for testing)")

    print("\nLeadFlow preflight\n" + "-" * 72)
    for level, name, detail in checks:
        print(f"{level:4}  {name:28} {detail}")
    failures = sum(level == "FAIL" for level, _, _ in checks)
    warnings = sum(level == "WARN" for level, _, _ in checks)
    print("-" * 72)
    print(f"Result: {failures} failure(s), {warnings} warning(s).")
    if failures or (args.strict and warnings):
        return 1
    return 0


def run_local(args: argparse.Namespace, file_values: dict[str, str], generated_password: str | None) -> int:
    env = local_runtime_env(file_values, args.host, args.port)
    if args.browser_audit:
        env["BROWSER_AUDIT_ENABLED"] = "true"
    # Make .env values available to subprocesses without overwriting explicit shell variables.
    for key, value in file_values.items():
        env.setdefault(key, value)
    env["AI_PROVIDER"] = args.ai_runtime
    run_database_migrations(
        env,
        use_venv=not args.no_install,
        skip=args.skip_migrations,
    )
    runtime_process = None
    runtime_name = args.ai_runtime
    if args.ai_runtime == "llamacpp":
        runtime_process = maybe_start_llamacpp(env, args.skip_local_ai)
    elif args.ai_runtime == "ollama":
        runtime_process = maybe_start_ollama(env, args.skip_local_ai, args.skip_model_pull)
    else:
        log("Using the configured hosted OpenAI-compatible provider; no local AI runtime is started.")
    python = str(project_python(use_venv=not args.no_install))
    command = [
        python, "-m", "uvicorn", "app.main:app", "--host", args.host, "--port", str(args.port), "--workers", "1"
    ]
    if args.reload:
        command = [python, "-m", "uvicorn", "app.main:app", "--host", args.host, "--port", str(args.port), "--reload"]
    log("Starting backend and server-rendered frontend in one FastAPI/Uvicorn process.")
    app_process = subprocess.Popen(command, cwd=ROOT, env=env)
    public_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    base_url = f"http://{public_host}:{args.port}"
    try:
        startup_timeout = max(45, int(env.get("STARTUP_HEALTH_TIMEOUT_SECONDS", "120")))
        if wait_for_process_url(app_process, base_url + "/health", startup_timeout):
            print("\nLeadFlow is ready:")
            print(f"  Frontend dashboard: {base_url}")
            print(f"  Backend API docs:   {base_url}/docs")
            print(f"  Health endpoint:    {base_url}/health")
            print(f"  Admin username:     {file_values.get('ADMIN_USERNAME', 'admin')}")
            if generated_password:
                print(f"  Generated password: {generated_password}")
            else:
                print("  Admin password:     stored in .env")
            print("\nPress Ctrl+C to stop.\n", flush=True)
            if not args.no_browser:
                with suppress(Exception):
                    webbrowser.open(base_url)
        else:
            if app_process.poll() is not None:
                log(f"The web server exited during startup with code {app_process.returncode}. Check the logs above.")
            else:
                log(
                    f"The web server is still running but did not become healthy within {startup_timeout} seconds. "
                    "Do not start a second instance; check the startup phase logs above."
                )
        return app_process.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        terminate(app_process, "LeadFlow web server")
        terminate(runtime_process, f"{runtime_name} server started by LeadFlow")


def docker_command(args: argparse.Namespace) -> list[str]:
    if args.ai_runtime == "llamacpp":
        command = ["docker", "compose", "-f", "docker-compose.llamacpp.yml"]
        if args.gpu == "nvidia":
            command += ["-f", "docker-compose.llamacpp.gpu.yml"]
        elif args.gpu == "amd":
            command += ["-f", "docker-compose.llamacpp.amd.yml"]
    else:
        command = ["docker", "compose", "-f", "docker-compose.yml"]
        if args.gpu == "nvidia":
            command += ["-f", "docker-compose.gpu.yml"]
        elif args.gpu == "amd":
            command += ["-f", "docker-compose.amd.yml"]
    if args.browser_audit:
        command += ["-f", "docker-compose.browser.yml"]
    if args.production:
        command += ["-f", "docker-compose.production.yml"]
        if args.browser_audit:
            command += ["-f", "docker-compose.browser.worker.yml"]
    if args.observability:
        command += ["-f", "docker-compose.observability.yml"]
    command += ["up", "--build"]
    if args.detach:
        command.append("-d")
    return command


def run_docker(args: argparse.Namespace, generated_password: str | None) -> int:
    if not shutil.which("docker"):
        raise SystemExit("Docker is not installed or not in PATH")
    try:
        subprocess.check_call(["docker", "compose", "version"], cwd=ROOT, stdout=subprocess.DEVNULL)
    except subprocess.CalledProcessError as exc:
        raise SystemExit("Docker Compose v2 is required") from exc
    command = docker_command(args)
    runtime_env = os.environ.copy()
    runtime_env["AI_PROVIDER"] = args.ai_runtime
    if args.ai_runtime == "llamacpp" and args.gpu in {"nvidia", "amd"}:
        runtime_env["LLAMACPP_N_GPU_LAYERS"] = "999"
    log(f"Starting frontend, backend, {args.ai_runtime}, and model setup with Docker Compose.")
    if generated_password:
        print(f"Generated admin password (also stored in .env): {generated_password}")
    print("Dashboard after startup: http://127.0.0.1:8000")
    return subprocess.call(command, cwd=ROOT, env=runtime_env)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install and start the complete LeadFlow application")
    parser.add_argument("--docker", action="store_true", help="Use Docker Compose for the app and selected local AI runtime")
    parser.add_argument(
        "--ai-runtime",
        choices=["ollama", "llamacpp", "openai_compatible"],
        default=None,
        help="AI provider/runtime (default comes from .env; openai_compatible supports Groq)",
    )
    parser.add_argument("--gpu", choices=["cpu", "nvidia", "amd"], default="cpu", help="Docker local-AI hardware mode")
    parser.add_argument("--detach", action="store_true", help="Start Docker services in the background")
    parser.add_argument("--production", action="store_true", help="Add PostgreSQL, Redis, RQ worker, and dedicated scheduler")
    parser.add_argument("--observability", action="store_true", help="Add Prometheus and Grafana (Docker only)")
    parser.add_argument("--host", default="127.0.0.1", help="Local Uvicorn bind host")
    parser.add_argument("--port", type=int, default=8000, help="Local Uvicorn port")
    parser.add_argument("--reload", action="store_true", help="Enable Uvicorn development reload")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the dashboard automatically")
    parser.add_argument("--no-install", action="store_true", help="Use the current Python environment without creating/installing .venv")
    parser.add_argument("--force-install", action="store_true", help="Reinstall Python requirements even if unchanged")
    parser.add_argument("--install-only", action="store_true", help="Create .env/.venv, install requirements, then exit")
    parser.add_argument("--preflight", action="store_true", help="Install/check local requirements and configuration, then exit")
    parser.add_argument("--strict", action="store_true", help="Make preflight warnings return a non-zero exit code")
    parser.add_argument(
        "--setup-free-portal",
        action="store_true",
        help="Run the one-command Neon + private GitHub + Render public-portal wizard",
    )
    parser.add_argument(
        "--rotate-portal-credentials",
        action="store_true",
        help="Rotate Neon password and APP_SECRET after accidental disclosure, then repair Render",
    )
    parser.add_argument(
        "--repair-render-portal",
        action="store_true",
        help="Repair only the existing Render service; skip Neon migration and GitHub setup",
    )
    parser.add_argument(
        "--update-public-portal",
        action="store_true",
        help="Push updated public-portal code to the existing GitHub repository without changing secrets",
    )
    parser.add_argument("--skip-migrations", action="store_true", help="Do not automatically run Alembic for local PostgreSQL")
    parser.add_argument("--skip-local-ai", "--skip-ollama", dest="skip_local_ai", action="store_true", help="Do not start/check the selected local AI runtime in non-Docker mode")
    parser.add_argument("--skip-model-pull", action="store_true", help="Do not run ollama pull in non-Docker mode")
    parser.add_argument("--browser-audit", action="store_true", help="Enable Playwright mobile rendering/screenshots (larger install)")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    portal_actions = sum(
        bool(value)
        for value in (
            args.setup_free_portal,
            args.rotate_portal_credentials,
            args.repair_render_portal,
            args.update_public_portal,
        )
    )
    if portal_actions > 1:
        parser.error("Choose only one portal setup, repair, or credential-rotation action")
    if portal_actions and (args.docker or args.preflight or args.install_only):
        parser.error("Portal setup/repair is local and cannot be combined with Docker/preflight/install-only")
    os.chdir(ROOT)
    file_values, generated_password = ensure_env_file()
    if args.ai_runtime is None:
        configured_runtime = file_values.get("AI_PROVIDER", "ollama").lower()
        supported = {"ollama", "llamacpp", "openai_compatible"}
        args.ai_runtime = configured_runtime if configured_runtime in supported else "ollama"
    if generated_password:
        os.environ["LEADFLOW_GENERATED_ADMIN_PASSWORD"] = generated_password
    else:
        generated_password = os.environ.get("LEADFLOW_GENERATED_ADMIN_PASSWORD")
    if args.docker:
        if args.preflight:
            return run_preflight(args, file_values)
        return run_docker(args, generated_password)
    if not args.no_install:
        install_python_requirements(args.force_install)
    if repair_known_env_format_errors():
        file_values = read_env()
    validate_project_configuration(file_values, use_venv=not args.no_install)
    if not portal_actions:
        portal_status, portal_message = public_portal_setup_status(file_values)
        if portal_status == "incomplete":
            log(f"Public portal setup is incomplete: {portal_message}")
    if args.browser_audit:
        install_playwright_browser(use_venv=not args.no_install)
    if args.update_public_portal:
        file_values = update_public_portal_source(args)
        return run_local(args, file_values, generated_password)
    if args.repair_render_portal:
        file_values = repair_existing_render_portal(args)
        return run_local(args, file_values, generated_password)
    if args.rotate_portal_credentials:
        file_values = rotate_public_portal_credentials(args)
        return run_local(args, file_values, generated_password)
    if args.setup_free_portal:
        file_values = setup_free_public_portal(args)
        return run_local(args, file_values, generated_password)
    if args.preflight:
        return run_preflight(args, file_values)
    if args.install_only:
        log("Installation completed.")
        if generated_password:
            print(f"Generated admin password (also stored in .env): {generated_password}")
        return 0
    return run_local(args, file_values, generated_password)


if __name__ == "__main__":
    raise SystemExit(main())
