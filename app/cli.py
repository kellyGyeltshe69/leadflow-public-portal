from __future__ import annotations

import argparse
import json

from .ai import ai_backend_report
from .composio_gateway import ComposioGateway
from .config import get_settings
from .db import init_db
from .pipeline import (
    discover_job,
    ensure_default_campaign,
    fast_start_job,
    send_due_job,
    sync_replies_job,
)
from .runtime import ensure_system_state, get_runtime_mode, set_runtime_mode
from .sheets import sync_google_sheet_job


def main() -> None:
    parser = argparse.ArgumentParser(description="LeadFlow maintenance commands")
    parser.add_argument(
        "command",
        choices=["discover", "fast-start", "send", "sync", "sheet", "connections", "ai", "mode-status", "mode-demo", "mode-live", "gates"],
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    init_db()
    ensure_system_state()
    ensure_default_campaign()
    if args.command == "discover":
        result = discover_job(force=args.force)
    elif args.command == "fast-start":
        result = fast_start_job()
    elif args.command == "send":
        result = send_due_job()
    elif args.command == "sync":
        result = sync_replies_job()
    elif args.command == "sheet":
        result = sync_google_sheet_job()
    elif args.command == "connections":
        result = ComposioGateway().connection_report()
    elif args.command == "ai":
        result = ai_backend_report(demo_mode=get_runtime_mode() == "demo")
    elif args.command == "mode-status":
        result = {"runtime_mode": get_runtime_mode()}
    elif args.command == "mode-demo":
        result = {"runtime_mode": set_runtime_mode("demo")}
    elif args.command == "mode-live":
        result = {"runtime_mode": set_runtime_mode("live"), "warning": "CLI mode switch bypasses dashboard readiness checks; keep SENDING_ENABLED=false."}
    else:
        settings = get_settings()
        runtime_mode = get_runtime_mode()
        demo_mode = runtime_mode == "demo"
        result = {
            "runtime_mode": runtime_mode,
            "demo_mode": demo_mode,
            "sending_enabled": settings.sending_enabled,
            "fast_start_enabled": settings.fast_start_enabled,
            "startup_discovery_target": settings.startup_discovery_target,
            "configured_total_send_limit": settings.daily_total_send_limit,
            "effective_total_send_limit": settings.effective_total_send_limit,
            "production_send_ready": settings.production_send_ready_for(demo_mode),
            "postal_ready": settings.postal_ready,
            "composio_ready": settings.composio_ready,
            "gmail_via_composio_configured": settings.gmail_ready,
            "google_maps_via_composio_configured": settings.composio_ready and not demo_mode,
            "google_sheets_configured": settings.sheets_configured,
            "ai_ready": settings.ai_ready_for(demo_mode),
            "public_https": settings.public_url_ready,
            "admin_security_ready": settings.admin_security_ready,
        }
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
