from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

from .config import get_settings

TOOLKITS = {"gmail", "google_maps", "googlesheets"}


@dataclass
class ConnectionInfo:
    id: str
    toolkit: str
    status: str
    alias: str | None = None
    updated_at: str | None = None


class ComposioGateway:
    """Small deterministic wrapper around Composio-managed Google connections.

    LeadFlow—not the LLM—chooses every action and request. Composio stores and
    refreshes Google credentials; this wrapper pins each request to one explicit
    connected account whenever an ID is configured.
    """

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.composio_api_key:
            raise RuntimeError("COMPOSIO_API_KEY is not configured")
        try:
            from composio import Composio
        except ImportError as exc:
            raise RuntimeError("The composio Python package is not installed") from exc
        self.settings = settings
        self.client = Composio(
            api_key=settings.composio_api_key,
            toolkit_versions={
                "gmail": settings.composio_gmail_version,
                "google_maps": settings.composio_google_maps_version,
                "googlesheets": settings.composio_sheets_version,
            },
        )

    def _explicit_id(self, toolkit: str) -> str:
        values = {
            "gmail": self.settings.composio_gmail_connection_id,
            "google_maps": self.settings.composio_google_maps_connection_id,
            "googlesheets": self.settings.composio_sheets_connection_id,
        }
        return values.get(toolkit, "").strip()

    def active_connections(self, toolkit: str | None = None) -> list[ConnectionInfo]:
        if toolkit and toolkit not in TOOLKITS:
            raise ValueError(f"Unsupported toolkit: {toolkit}")
        kwargs: dict[str, Any] = {
            "user_ids": [self.settings.composio_user_id],
            "statuses": ["ACTIVE"],
            "limit": 100,
            "order_by": "updated_at",
            "order_direction": "desc",
        }
        if toolkit:
            kwargs["toolkit_slugs"] = [toolkit]
        response = self.client.connected_accounts.list(**kwargs)
        return [
            ConnectionInfo(
                id=item.id,
                toolkit=item.toolkit.slug,
                status=item.status,
                alias=item.alias,
                updated_at=item.updated_at,
            )
            for item in response.items
            if item.toolkit.slug in TOOLKITS
        ]

    def resolve_connection_id(self, toolkit: str) -> str:
        if toolkit not in TOOLKITS:
            raise ValueError(f"Unsupported toolkit: {toolkit}")
        explicit = self._explicit_id(toolkit)
        if explicit:
            # Confirm that the pinned account is still ACTIVE and belongs to this app user.
            items = self.client.connected_accounts.list(
                connected_account_ids=[explicit],
                user_ids=[self.settings.composio_user_id],
                statuses=["ACTIVE"],
                limit=10,
            ).items
            if not items:
                raise RuntimeError(f"Pinned {toolkit} connection {explicit} is not ACTIVE for the configured Composio user")
            if items[0].toolkit.slug != toolkit:
                raise RuntimeError(f"Pinned connection {explicit} is for {items[0].toolkit.slug}, not {toolkit}")
            return explicit
        matches = self.active_connections(toolkit)
        if not matches:
            raise RuntimeError(f"No ACTIVE Composio {toolkit} connection was found")
        if len(matches) > 1:
            raise RuntimeError(
                f"Multiple ACTIVE {toolkit} connections exist. Set the corresponding COMPOSIO_*_CONNECTION_ID to avoid using the wrong account."
            )
        return matches[0].id

    def connection_report(self) -> dict[str, dict]:
        report: dict[str, dict] = {}
        for toolkit in sorted(TOOLKITS):
            try:
                connection_id = self.resolve_connection_id(toolkit)
                report[toolkit] = {"active": True, "connection_id": connection_id, "error": ""}
            except Exception as exc:
                report[toolkit] = {"active": False, "connection_id": "", "error": str(exc)}
        return report

    def proxy(
        self,
        toolkit: str,
        endpoint: str,
        method: str,
        *,
        body: object | None = None,
        parameters: list[dict] | None = None,
    ) -> Any:
        connection_id = self.resolve_connection_id(toolkit)
        proxy_method = cast(Literal["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD"], method)
        response = self.client.tools.proxy(
            endpoint=endpoint,
            method=proxy_method,
            body=body,
            connected_account_id=connection_id,
            parameters=cast(Any, parameters),
        )
        status = int(response.status)
        if status >= 400:
            raise RuntimeError(f"Composio {toolkit} proxy returned HTTP {status}: {response.data}")
        return response.data

    def create_connect_link(self, toolkit: str) -> str:
        if toolkit not in TOOLKITS:
            raise ValueError(f"Unsupported toolkit: {toolkit}")
        auth_config_ids = {
            "gmail": self.settings.composio_gmail_auth_config_id,
            "google_maps": self.settings.composio_google_maps_auth_config_id,
            "googlesheets": self.settings.composio_sheets_auth_config_id,
        }
        auth_config_id = auth_config_ids[toolkit].strip()
        if not auth_config_id:
            raise RuntimeError(f"Set the Composio auth config ID for {toolkit} before creating a Connect Link")
        request = self.client.connected_accounts.link(
            user_id=self.settings.composio_user_id,
            auth_config_id=auth_config_id,
            callback_url=f"{self.settings.public_base_url.rstrip('/')}/integrations",
            alias=f"leadflow-{toolkit}",
            allow_multiple=False,
        )
        if not request.redirect_url:
            raise RuntimeError(f"Composio did not return a redirect URL for {toolkit}")
        return request.redirect_url
