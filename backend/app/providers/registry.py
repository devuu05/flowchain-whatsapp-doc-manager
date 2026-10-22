"""Resolves provider implementations for a tenant's integration configs.

Tests can install overrides with `set_override`; fakes are refused when
ALLOW_FAKE_PROVIDERS is false (always in production).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utcnow
from app.db.session import get_sessionmaker
from app.models import IntegrationConfig, IntegrationKind, IntegrationStatus, Tenant
from app.providers.ai.base import DocumentClassifier
from app.providers.ai.gemini import GeminiClassifier
from app.providers.ai.rules import RulesClassifier
from app.providers.errors import ProviderError
from app.providers.storage.base import DocumentStorage
from app.providers.storage.fake import FakeStorage
from app.providers.storage.google_drive import GoogleDriveStorage
from app.providers.whatsapp.base import WhatsAppProvider
from app.providers.whatsapp.fake import FakeWhatsAppProvider
from app.providers.whatsapp.wasender import WasenderProvider
from app.security.crypto import decrypt_json, encrypt_json

log = logging.getLogger(__name__)

_overrides: dict[str, Callable[..., Any]] = {}
_fake_whatsapp: dict[uuid.UUID, FakeWhatsAppProvider] = {}
_fake_storage: dict[uuid.UUID, FakeStorage] = {}


def set_override(kind: str, factory: Callable[..., Any] | None) -> None:
    """kind in {"whatsapp", "storage", "classifier"}; factory receives the config/tenant."""
    if factory is None:
        _overrides.pop(kind, None)
    else:
        _overrides[kind] = factory


def reset_fakes() -> None:
    _fake_whatsapp.clear()
    _fake_storage.clear()
    _overrides.clear()


def _require_fakes_allowed() -> None:
    if not get_settings().allow_fake_providers:
        raise ProviderError("fake providers are disabled", provider="fake", transient=False)


def whatsapp_for(conn: IntegrationConfig) -> WhatsAppProvider:
    if "whatsapp" in _overrides:
        provider: WhatsAppProvider = _overrides["whatsapp"](conn)
        return provider
    s = get_settings()
    if conn.provider == "wasender":
        secrets = decrypt_json(conn.encrypted_secrets)
        return WasenderProvider(
            api_key=secrets.get("api_key", ""),
            webhook_secret=secrets.get("webhook_secret", ""),
            base_url=s.wasender_base_url,
            timeout=s.wasender_timeout_seconds,
        )
    if conn.provider == "fake":
        _require_fakes_allowed()
        secrets = decrypt_json(conn.encrypted_secrets)
        return _fake_whatsapp.setdefault(
            conn.id, FakeWhatsAppProvider(secrets.get("webhook_secret", "fake-secret"))
        )
    raise ProviderError(
        f"unknown WhatsApp provider {conn.provider}", provider="registry", transient=False
    )


def fake_whatsapp(conn_id: uuid.UUID) -> FakeWhatsAppProvider:
    return _fake_whatsapp.setdefault(conn_id, FakeWhatsAppProvider())


def fake_storage(conn_id: uuid.UUID) -> FakeStorage:
    return _fake_storage.setdefault(conn_id, FakeStorage())


def drive_config(db: Session, tenant_id: uuid.UUID) -> IntegrationConfig | None:
    return db.scalar(
        select(IntegrationConfig).where(
            IntegrationConfig.tenant_id == tenant_id,
            IntegrationConfig.kind == IntegrationKind.google_drive,
            IntegrationConfig.status.in_([IntegrationStatus.connected, IntegrationStatus.error]),
        )
    )


def _persist_refreshed_tokens(config_id: uuid.UUID) -> Callable[[dict[str, Any]], None]:
    def persist(secrets: dict[str, Any]) -> None:
        # Separate short transaction so refreshed tokens survive a rollback of the job.
        with get_sessionmaker()() as db:
            cfg = db.get(IntegrationConfig, config_id)
            if cfg is not None:
                cfg.encrypted_secrets = encrypt_json(secrets)
                cfg.updated_at = utcnow()
                db.commit()

    return persist


def storage_for(db: Session, tenant_id: uuid.UUID) -> tuple[DocumentStorage, IntegrationConfig]:
    cfg = drive_config(db, tenant_id)
    if cfg is None:
        raise ProviderError(
            "Google Drive is not connected for this workspace", provider="storage", transient=False
        )
    if "storage" in _overrides:
        storage: DocumentStorage = _overrides["storage"](cfg)
        return storage, cfg
    if cfg.provider == "google_drive":
        root = cfg.settings.get("root_folder_id")
        if not root:
            raise ProviderError("Drive root folder missing", provider="storage", transient=False)
        return (
            GoogleDriveStorage(
                secrets=decrypt_json(cfg.encrypted_secrets),
                root_folder_id=root,
                on_tokens_refreshed=_persist_refreshed_tokens(cfg.id),
                timeout=get_settings().google_timeout_seconds,
            ),
            cfg,
        )
    if cfg.provider == "fake":
        _require_fakes_allowed()
        return fake_storage(cfg.id), cfg
    raise ProviderError(
        f"unknown storage provider {cfg.provider}", provider="registry", transient=False
    )


def classifier_for(tenant: Tenant) -> DocumentClassifier:
    """AI classifier only when the tenant has explicitly enabled AI and a key is configured."""
    if "classifier" in _overrides:
        classifier: DocumentClassifier = _overrides["classifier"](tenant)
        return classifier
    s = get_settings()
    if tenant.ai_enabled and s.gemini_api_key.get_secret_value():
        return GeminiClassifier(
            api_key=s.gemini_api_key.get_secret_value(),
            model=s.gemini_model,
            base_url=s.gemini_base_url,
            timeout=s.gemini_timeout_seconds,
            max_inline_bytes=s.gemini_max_inline_bytes,
        )
    return RulesClassifier()


def rules_classifier() -> DocumentClassifier:
    return RulesClassifier()
