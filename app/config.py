from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "LeadFlow"
    app_secret: str = "CHANGE-ME-BEFORE-PRODUCTION"
    admin_username: str = "admin"
    admin_password: str = "change-me"
    database_url: str = "sqlite:////data/leadflow.db"
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=200)
    database_pool_recycle_seconds: int = Field(default=1800, ge=60)
    auto_create_schema: bool = True
    redis_url: str = ""
    async_workers_enabled: bool = False
    public_base_url: str = "http://localhost:8000"
    public_report_base_url: str = ""

    jwt_secret: str = "CHANGE-ME-JWT-SECRET"
    jwt_algorithm: str = "HS256"
    jwt_access_minutes: int = Field(default=30, ge=5, le=1440)
    admin_email: str = "admin@localhost"
    encryption_key: str = ""
    api_rate_limit_per_minute: int = Field(default=120, ge=10, le=10000)
    auth_rate_limit_per_minute: int = Field(default=10, ge=1, le=100)
    google_oauth_enabled: bool = False
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = ""
    metrics_enabled: bool = True
    metrics_token: str = ""
    landing_pages_enabled: bool = True

    sender_name: str = "David Brown"
    sender_role: str = "Independent Hostinger Affiliate"
    sender_email: str = ""
    # Zero-cost mode: Gmail's authenticated primary address is the visible
    # From identity; SENDER_EMAIL remains the verified business Reply-To alias.
    gmail_free_primary_from_mode: bool = True
    html_email_enabled: bool = True
    physical_postal_address: str = ""
    postal_address_attested: bool = False
    affiliate_url: str = "https://www.hostinger.com?REFERRALCODE=ZEVDAVIDBEXE"

    demo_mode: bool = True
    sending_enabled: bool = False
    # Twelve new sequences/day is sustainable under a 50-message cap when all
    # four stages are eventually due (12 x 4 = 48), before reply suppression.
    daily_new_lead_limit: int = Field(default=12, ge=1, le=50)
    daily_total_send_limit: int = Field(default=50, ge=1, le=200)
    minimum_lead_score: float = Field(default=7.0, ge=1, le=10)
    high_volume_acknowledged: bool = False
    mailbox_authentication_confirmed: bool = False

    fast_start_enabled: bool = True
    startup_discovery_target: int = Field(default=250, ge=1, le=500)
    startup_discovery_cooldown_hours: int = Field(default=24, ge=1, le=168)
    startup_send_approved: bool = True
    send_window_start_hour: int = Field(default=9, ge=0, le=23)
    send_window_end_hour: int = Field(default=16, ge=1, le=23)
    send_timezone_fallback: str = "America/New_York"
    discovery_hour_utc: int = Field(default=13, ge=0, le=23)

    target_locations: str = (
        "Los Angeles CA|Dallas TX|Miami FL|Atlanta GA|Chicago IL|Denver CO|"
        "Raleigh NC|Phoenix AZ|Portland OR|Columbus OH"
    )
    target_industries: str = (
        "bakery|independent restaurant|hair salon|pet groomer|event venue|"
        "home contractor|mobile auto detailer|cleaning service|boutique|cafe"
    )

    # Composio manages the Google Maps, Gmail and Google Sheets credentials.
    composio_api_key: str = ""
    composio_user_id: str = "david-brown-leadflow"
    composio_gmail_connection_id: str = ""
    composio_google_maps_connection_id: str = ""
    composio_sheets_connection_id: str = ""
    composio_gmail_auth_config_id: str = ""
    composio_google_maps_auth_config_id: str = ""
    composio_sheets_auth_config_id: str = ""
    # Pinned toolkit versions make programmatic response parsing predictable.
    composio_gmail_version: str = "20260721_00"
    composio_google_maps_version: str = "20260721_00"
    composio_sheets_version: str = "20260721_00"
    google_sheet_id: str = ""
    google_sheet_leads_tab: str = "Leads"
    google_sheet_outreach_tab: str = "Outreach"

    # Licensed web search is optional enrichment; Google Maps discovery uses Composio.
    search_provider: str = "serper"
    serper_api_key: str = ""
    brave_search_api_key: str = ""
    bing_search_api_key: str = ""
    bing_search_endpoint: str = "https://api.bing.microsoft.com/v7.0/search"
    extended_source_discovery: bool = False

    # Local Ollama is the default AI backend. The app container reaches the
    # private Compose service by its service name; port 11434 is not published.
    ai_provider: str = "ollama"
    email_ai_quality_gate_enabled: bool = True
    email_ai_min_quality_score: int = Field(default=85, ge=50, le=100)
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3:4b-instruct"
    ollama_num_ctx: int = Field(default=4096, ge=1024, le=131072)
    ollama_timeout_seconds: int = 180

    # Optional llama.cpp backend with direct GGUF/quantization and GPU-layer control.
    llamacpp_base_url: str = "http://llama-cpp:8080"
    llamacpp_model_alias: str = "leadflow-local"
    llamacpp_api_key: str = "change-me-local"
    llamacpp_hf_model: str = "ggml-org/Qwen3-4B-GGUF:Q4_K_M"
    llamacpp_num_ctx: int = Field(default=4096, ge=1024, le=131072)
    llamacpp_n_gpu_layers: int = Field(default=0, ge=0, le=999)
    llamacpp_timeout_seconds: int = 300

    # Optional paid/hosted fallback. It is unused while AI_PROVIDER is local.
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    openai_timeout_seconds: int = 60

    audit_user_agent: str = "LeadFlowWebsiteReview/0.2 (+public business website review)"
    audit_timeout_seconds: int = 15
    audit_link_check_limit: int = Field(default=12, ge=0, le=50)
    audit_link_workers: int = Field(default=4, ge=1, le=8)
    respect_robots_txt: bool = True

    # Optional mobile browser audit. Disabled in the lightweight image.
    browser_audit_enabled: bool = False
    browser_audit_timeout_seconds: int = 35
    browser_viewport_width: int = Field(default=390, ge=240, le=1200)
    browser_viewport_height: int = Field(default=844, ge=320, le=2000)
    browser_screenshot_dir: str = "/data/screenshots"

    scheduler_enabled: bool = True
    reply_sync_minutes: int = 10
    send_queue_minutes: int = 5
    sheet_sync_minutes: int = 15
    discovery_check_minutes: int = 30

    @property
    def locations(self) -> list[str]:
        return [x.strip() for x in self.target_locations.split("|") if x.strip()]

    @property
    def industries(self) -> list[str]:
        return [x.strip() for x in self.target_industries.split("|") if x.strip()]

    @property
    def postal_ready(self) -> bool:
        value = self.physical_postal_address.strip().lower()
        return bool(
            self.postal_address_attested
            and value
            and "required" not in value
            and "placeholder" not in value
        )

    @property
    def composio_ready(self) -> bool:
        return bool(self.composio_api_key.strip() and self.composio_user_id.strip())

    @property
    def gmail_ready(self) -> bool:
        # This is the configuration gate only. GmailClient checks the live
        # primary address or accepted send-as alias before every send/sync batch.
        return bool(self.composio_ready and self.sender_email.strip())

    @property
    def sheets_configured(self) -> bool:
        return bool(self.composio_ready and self.google_sheet_id.strip())

    def ai_ready_for(self, demo_mode: bool) -> bool:
        if demo_mode:
            return False
        provider = self.ai_provider.strip().lower()
        if provider == "ollama":
            return bool(self.ollama_base_url.strip() and self.ollama_model.strip())
        if provider == "llamacpp":
            return bool(
                self.llamacpp_base_url.strip()
                and self.llamacpp_model_alias.strip()
                and self.llamacpp_api_key.strip()
            )
        if provider == "openai_compatible":
            return bool(self.openai_base_url.strip() and self.openai_api_key.strip() and self.openai_model.strip())
        return False

    @property
    def ai_ready(self) -> bool:
        return self.ai_ready_for(self.demo_mode)

    @property
    def discovery_ready(self) -> bool:
        # Live Google Maps connection status is checked at job execution.
        return self.composio_ready and not self.demo_mode

    @property
    def admin_security_ready(self) -> bool:
        return all([
            self.app_secret != "CHANGE-ME-BEFORE-PRODUCTION",
            self.admin_password != "change-me",
            self.jwt_secret != "CHANGE-ME-JWT-SECRET",
            len(self.app_secret) >= 32,
            len(self.jwt_secret) >= 32,
            len(self.admin_password) >= 16,
        ])

    @staticmethod
    def _public_https_url(value: str) -> bool:
        try:
            parsed = urlparse(value)
        except ValueError:
            return False
        host = (parsed.hostname or "").lower()
        blocked_hosts = {
            "localhost",
            "127.0.0.1",
            "leads.yourdomain.com",
            "dashboard.render.com",
            "console.neon.tech",
            "github.com",
        }
        return bool(
            parsed.scheme == "https"
            and host
            and host not in blocked_hosts
            and "yourdomain" not in host
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )

    @property
    def public_url_ready(self) -> bool:
        return self._public_https_url(self.public_base_url)

    @property
    def report_base_url(self) -> str:
        return (self.public_report_base_url or self.public_base_url).rstrip("/")

    @property
    def report_url_ready(self) -> bool:
        return self._public_https_url(self.report_base_url)

    @property
    def effective_total_send_limit(self) -> int:
        if self.daily_total_send_limit > 30 and not (
            self.high_volume_acknowledged and self.mailbox_authentication_confirmed
        ):
            return 30
        return self.daily_total_send_limit

    def production_send_ready_for(self, demo_mode: bool) -> bool:
        return all([
            self.sending_enabled,
            not demo_mode,
            self.postal_ready,
            self.gmail_ready,
            self.admin_security_ready,
            self.report_url_ready,
        ])

    @property
    def production_send_ready(self) -> bool:
        return self.production_send_ready_for(self.demo_mode)


@lru_cache
def get_settings() -> Settings:
    return Settings()
