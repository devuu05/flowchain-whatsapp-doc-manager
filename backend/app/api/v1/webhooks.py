"""Inbound WhatsApp webhooks. Verify, dedupe, persist, enqueue — then return quickly."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from app.api.deps import DB
from app.core.config import get_settings
from app.core.errors import AppError, Forbidden, NotFound
from app.models import IntegrationConfig, IntegrationKind, IntegrationStatus
from app.providers import registry
from app.schemas.events import NormalizedEvent
from app.security.crypto import constant_time_equals
from app.security.rate_limit import limit
from app.services.ingest import ingest_events

router = APIRouter(prefix="/webhooks", tags=["webhooks"], dependencies=[Depends(limit("webhook"))])
log = logging.getLogger(__name__)


class WebhookAck(BaseModel):
    status: str = "ok"
    accepted: int = 0
    duplicate: int = 0
    ignored: int = 0


def _connection(db: DB, key: str) -> IntegrationConfig:
    if not 16 <= len(key) <= 64:
        raise NotFound()
    conn = db.scalar(
        select(IntegrationConfig).where(
            IntegrationConfig.connection_key == key,
            IntegrationConfig.kind == IntegrationKind.whatsapp,
        )
    )
    if conn is None or conn.status == IntegrationStatus.disconnected:
        raise NotFound()
    return conn


@router.post("/{provider_name}/{connection_key}", response_model=WebhookAck)
async def provider_webhook(
    provider_name: str, connection_key: str, request: Request, db: DB
) -> WebhookAck:
    body = await request.body()
    headers = dict(request.headers)
    # DB work is synchronous; keep it off the event loop.
    return await run_in_threadpool(_handle, db, provider_name, connection_key, headers, body)


def _handle(
    db: DB, provider_name: str, connection_key: str, headers: dict[str, str], body: bytes
) -> WebhookAck:
    conn = _connection(db, connection_key)
    if conn.provider != provider_name:
        raise NotFound()
    provider = registry.whatsapp_for(conn)
    if not provider.verify_webhook(headers, body):
        log.warning("webhook_signature_invalid", extra={"connection_id": str(conn.id)})
        raise Forbidden("Invalid webhook signature", code="invalid_signature")
    try:
        payload: Any = json.loads(body or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        events = provider.parse_webhook(payload)
    except (ValueError, ValidationError) as exc:
        log.warning("webhook_unparseable", extra={"error": type(exc).__name__})
        raise AppError("Unrecognized webhook payload", code="invalid_payload") from exc
    counts = ingest_events(db, conn, events, payload)
    return WebhookAck(**counts)


class InternalEvent(BaseModel):
    """Documented internal schema: a normalized event addressed to a connection."""

    model_config = ConfigDict(extra="forbid")
    connection_key: str = Field(min_length=16, max_length=64)
    event: NormalizedEvent


@router.post("/whatsapp", response_model=WebhookAck)
def internal_webhook(body: InternalEvent, request: Request, db: DB) -> WebhookAck:
    """Provider-independent ingestion for testing/integration. Disabled unless
    INTERNAL_WEBHOOK_TOKEN is set; callers must send it in `X-Internal-Token`."""
    expected = get_settings().internal_webhook_token.get_secret_value()
    if not expected:
        raise NotFound()
    if not constant_time_equals(request.headers.get("x-internal-token"), expected):
        raise Forbidden("Invalid internal token")
    conn = _connection(db, body.connection_key)
    counts = ingest_events(db, conn, [body.event], None)
    return WebhookAck(**counts)
