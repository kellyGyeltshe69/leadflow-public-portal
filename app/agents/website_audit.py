from __future__ import annotations

from ..services.advanced_audit import AdvancedAuditService


class WebsiteAuditAgent:
    name = "website_audit"

    def analyze(self, url: str | None) -> dict:
        return AdvancedAuditService().audit(url)
