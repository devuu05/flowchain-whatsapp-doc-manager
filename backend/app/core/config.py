"""Environment-based application settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "Flow Chain Document Manager"
    log_level: str = "INFO"
    log_json: bool = True

    # Public base URL of this API (used for webhook URLs, OAuth redirect and signed media URLs).
    public_base_url: str = "http://localhost:8000"
    # Dashboard origin(s) allowed by CORS (comma separated). Empty = same-origin only.
    cors_origins: str = ""
    dashboard_url: str = "http://localhost:3000"

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/flowchain"
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # Secrets
    secret_key: SecretStr = SecretStr("dev-insecure-secret-key-change-me-0123456789")
    # Comma separated Fernet keys; first is used for encryption, all for decryption (rotation).
    field_encryption_keys: SecretStr = SecretStr("")

    # Sessions
    session_cookie_name: str = "fc_session"
    session_idle_minutes: int = 60 * 8
    session_absolute_hours: int = 24 * 7
    cookie_secure: bool = False
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    # Number of reverse-proxy hops in front of the API that append to X-Forwarded-For
    # (Render/most PaaS: 1). 0 = use the socket peer address. Used for audit IPs/rate limits.
    trusted_proxy_hops: int = 0

    # Request limits
    max_webhook_body_bytes: int = 1_000_000
    max_json_body_bytes: int = 1_000_000

    # Files
    max_file_bytes: int = 16 * 1024 * 1024
    max_pdf_pages: int = 200
    max_sheets: int = 50
    max_slides: int = 300
    max_extracted_chars: int = 200_000
    extraction_timeout_seconds: int = 60
    media_blob_ttl_hours: int = 72
    pending_media_ttl_hours: int = 24
    search_session_ttl_minutes: int = 30
    search_page_size: int = 10
    signed_media_url_ttl_seconds: int = 600
    legacy_converter: Literal["none", "soffice"] = "none"

    # Worker
    embedded_worker: bool = False
    worker_poll_seconds: float = 1.0
    worker_lease_seconds: int = 300
    worker_concurrency: int = 1
    job_max_attempts: int = 6
    job_backoff_base_seconds: float = 5.0
    job_backoff_max_seconds: float = 900.0

    # WhatsApp (Wasender)
    wasender_base_url: str = "https://www.wasenderapi.com/api"
    wasender_timeout_seconds: float = 20.0

    # Google OAuth / Drive
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_oauth_scopes: str = "https://www.googleapis.com/auth/drive.file"
    google_timeout_seconds: float = 60.0

    # Gemini
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_timeout_seconds: float = 60.0
    gemini_max_inline_bytes: int = 15 * 1024 * 1024

    # Internal normalized-event webhook (dev/testing only; disabled when token empty)
    internal_webhook_token: SecretStr = SecretStr("")

    # Bootstrap admin (used only by `python -m app.cli bootstrap`)
    allow_fake_providers: bool = Field(default=True)

    @field_validator("public_base_url", "dashboard_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.app_env == "production":
            if self.secret_key.get_secret_value().startswith("dev-insecure"):
                raise ValueError("SECRET_KEY must be set in production")
            if len(self.secret_key.get_secret_value()) < 32:
                raise ValueError("SECRET_KEY must be at least 32 characters")
            if not self.field_encryption_keys.get_secret_value():
                raise ValueError("FIELD_ENCRYPTION_KEYS must be set in production")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
            if self.allow_fake_providers:
                raise ValueError("ALLOW_FAKE_PROVIDERS must be false in production")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    @property
    def google_redirect_uri(self) -> str:
        # Routed through the dashboard's same-origin /api proxy so the browser sends the
        # session cookie; the callback requires the same session that started the flow.
        return f"{self.dashboard_url}/api/v1/integrations/google/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
