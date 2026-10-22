"""WhatsApp and Google Drive connection management. Secrets are write-only."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Query, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import DB, Admin, Member
from app.core.config import get_settings
from app.core.errors import AppError, Conflict, NotFound
from app.db.base import utcnow
from app.models import (
    IntegrationConfig,
    IntegrationKind,
    IntegrationStatus,
    Role,
    TenantMembership,
    User,
)
from app.providers import registry
from app.providers.errors import ProviderError
from app.providers.storage import google_oauth
from app.providers.storage.google_drive import GoogleDriveStorage
from app.schemas.api import IntegrationOut, OAuthStartOut, WhatsAppConnectIn
from app.security.crypto import decrypt_json, encrypt_json, new_token
from app.security.signed_urls import sign, verify
from app.services import audit
from app.services.tenancy import normalize_phone

router = APIRouter(tags=["integrations"])
log = logging.getLogger(__name__)
DRIVE_ROOT_NAME = "Flow Chain Documents"


def _out(cfg: IntegrationConfig) -> IntegrationOut:
    out = IntegrationOut.model_validate(cfg)
    out.has_secrets = bool(cfg.encrypted_secrets)
    if cfg.kind == IntegrationKind.whatsapp and cfg.connection_key:
        provider = "wasender" if cfg.provider == "wasender" else cfg.provider
        out.webhook_url = (
            f"{get_settings().public_base_url}/api/v1/webhooks/{provider}/{cfg.connection_key}"
        )
    return out


def _get(db: DB, tenant_id: uuid.UUID, integration_id: uuid.UUID) -> IntegrationConfig:
    cfg = db.scalar(
        select(IntegrationConfig).where(
            IntegrationConfig.id == integration_id, IntegrationConfig.tenant_id == tenant_id
        )
    )
    if cfg is None:
        raise NotFound()
    return cfg


@router.get("/tenants/{tenant_id}/integrations", response_model=list[IntegrationOut])
def list_integrations(ctx: Member, db: DB) -> list[IntegrationOut]:
    rows = db.scalars(
        select(IntegrationConfig)
        .where(IntegrationConfig.tenant_id == ctx.tenant_id)
        .order_by(IntegrationConfig.created_at)
    )
    return [_out(r) for r in rows]


@router.post(
    "/tenants/{tenant_id}/integrations/whatsapp", response_model=IntegrationOut, status_code=201
)
def connect_whatsapp(body: WhatsAppConnectIn, ctx: Admin, db: DB) -> IntegrationOut:
    s = get_settings()
    if body.provider == "fake" and not s.allow_fake_providers:
        raise AppError("Fake provider is disabled", code="provider_unavailable")
    if body.provider == "wasender" and not (body.api_key and body.webhook_secret):
        raise AppError(
            "api_key and webhook_secret are required for WasenderAPI", code="secrets_required"
        )
    try:
        phone = normalize_phone(body.phone_number)
    except ValueError as exc:
        raise AppError(str(exc), code="invalid_phone") from exc
    existing = db.scalars(
        select(IntegrationConfig).where(
            IntegrationConfig.kind == IntegrationKind.whatsapp,
            IntegrationConfig.status != IntegrationStatus.disconnected,
        )
    ).all()
    if any(c.settings.get("phone_number") == phone for c in existing):
        raise Conflict("This WhatsApp number is already connected to a workspace")
    secrets = {
        "api_key": body.api_key or "",
        "webhook_secret": body.webhook_secret or new_token(24),
    }
    cfg = IntegrationConfig(
        tenant_id=ctx.tenant_id,
        kind=IntegrationKind.whatsapp,
        provider=body.provider,
        name=body.name,
        status=IntegrationStatus.pending,
        connection_key=new_token(24),
        encrypted_secrets=encrypt_json(secrets),
        settings={"phone_number": phone},
    )
    db.add(cfg)
    audit.record(
        db,
        "integration.whatsapp_connected",
        tenant_id=ctx.tenant_id,
        actor_type="user",
        actor_id=ctx.user.id,
        ip=ctx.auth.ip,
        details={"provider": body.provider},
    )
    db.commit()
    return _out(cfg)


@router.patch(
    "/tenants/{tenant_id}/integrations/{integration_id}/whatsapp", response_model=IntegrationOut
)
def rotate_whatsapp_secrets(
    integration_id: uuid.UUID, body: WhatsAppConnectIn, ctx: Admin, db: DB
) -> IntegrationOut:
    cfg = _get(db, ctx.tenant_id, integration_id)
    if cfg.kind != IntegrationKind.whatsapp:
        raise NotFound()
    secrets = decrypt_json(cfg.encrypted_secrets)
    if body.api_key:
        secrets["api_key"] = body.api_key
    if body.webhook_secret:
        secrets["webhook_secret"] = body.webhook_secret
    cfg.encrypted_secrets = encrypt_json(secrets)
    cfg.name = body.name
    audit.record(
        db,
        "integration.secrets_rotated",
        tenant_id=ctx.tenant_id,
        actor_type="user",
        actor_id=ctx.user.id,
        ip=ctx.auth.ip,
        target_type="integration",
        target_id=cfg.id,
    )
    db.commit()
    return _out(cfg)


@router.post(
    "/tenants/{tenant_id}/integrations/{integration_id}/check", response_model=IntegrationOut
)
def check_integration(integration_id: uuid.UUID, ctx: Admin, db: DB) -> IntegrationOut:
    cfg = _get(db, ctx.tenant_id, integration_id)
    if cfg.status == IntegrationStatus.disconnected:
        raise Conflict("Integration is disconnected")
    try:
        if cfg.kind == IntegrationKind.whatsapp:
            st = registry.whatsapp_for(cfg).connection_status()
            ok, detail = st.connected, st.detail or st.status
        else:
            storage, _ = registry.storage_for(db, ctx.tenant_id)
            ok, detail = storage.check()
    except ProviderError as exc:
        ok, detail = False, str(exc)
    cfg.status = IntegrationStatus.connected if ok else IntegrationStatus.error
    cfg.last_error = None if ok else (detail or "check failed")[:1000]
    cfg.last_checked_at = utcnow()
    db.commit()
    return _out(cfg)


@router.delete("/tenants/{tenant_id}/integrations/{integration_id}", status_code=204)
def disconnect(integration_id: uuid.UUID, ctx: Admin, db: DB) -> Response:
    cfg = _get(db, ctx.tenant_id, integration_id)
    if cfg.provider == "google_drive" and cfg.encrypted_secrets:
        token = decrypt_json(cfg.encrypted_secrets).get("refresh_token")
        if token:
            google_oauth.revoke(token)
    cfg.status = IntegrationStatus.disconnected
    cfg.encrypted_secrets = None
    cfg.connection_key = None
    audit.record(
        db,
        "integration.disconnected",
        tenant_id=ctx.tenant_id,
        actor_type="user",
        actor_id=ctx.user.id,
        ip=ctx.auth.ip,
        target_type="integration",
        target_id=cfg.id,
        details={"kind": cfg.kind},
    )
    db.commit()
    return Response(status_code=204)


# ---- Google Drive OAuth -----------------------------------------------------------------
@router.post("/tenants/{tenant_id}/integrations/google/start", response_model=OAuthStartOut)
def google_start(ctx: Admin) -> OAuthStartOut:
    s = get_settings()
    if not s.google_client_id or not s.google_client_secret.get_secret_value():
        raise AppError("Google OAuth is not configured on the server", code="oauth_unconfigured")
    state = sign("google_oauth", {"t": str(ctx.tenant_id), "u": str(ctx.user.id)}, 600)
    return OAuthStartOut(authorization_url=google_oauth.build_authorization_url(state))


def _redirect(status: str, tenant_id: uuid.UUID | None = None) -> RedirectResponse:
    base = get_settings().dashboard_url
    if tenant_id is None:
        return RedirectResponse(f"{base}/?drive={status}", status_code=303)
    return RedirectResponse(f"{base}/t/{tenant_id}/settings/drive?result={status}", status_code=303)


@router.get("/integrations/google/callback", include_in_schema=False)
def google_callback(
    db: DB,
    state: str = Query(max_length=1000),
    code: str | None = Query(default=None, max_length=2000),
    error: str | None = Query(default=None, max_length=200),
) -> RedirectResponse:
    data = verify("google_oauth", state)
    if data is None:
        return _redirect("invalid_state")
    tenant_id, user_id = uuid.UUID(data["t"]), uuid.UUID(data["u"])
    if error or not code:
        return _redirect("denied", tenant_id)
    user = db.get(User, user_id)
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == user_id
        )
    )
    allowed = (
        user is not None
        and user.is_active
        and (
            user.is_platform_admin
            or (membership is not None and membership.role in (Role.owner, Role.admin))
        )
    )
    if not allowed:
        return _redirect("forbidden", tenant_id)
    try:
        tokens = google_oauth.exchange_code(code)
        if not tokens.refresh_token:
            return _redirect("no_refresh_token", tenant_id)
        cfg = registry.drive_config(db, tenant_id)
        root_id = cfg.settings.get("root_folder_id") if cfg else None
        if not root_id:
            root_id = GoogleDriveStorage.create_root_folder(tokens.access_token, DRIVE_ROOT_NAME)
    except ProviderError as exc:
        log.warning("google_oauth_failed", extra={"error": str(exc)})
        return _redirect("error", tenant_id)
    if cfg is None:
        cfg = IntegrationConfig(
            tenant_id=tenant_id,
            kind=IntegrationKind.google_drive,
            provider="google_drive",
            name="Google Drive",
            settings={},
        )
        db.add(cfg)
    cfg.encrypted_secrets = encrypt_json(tokens.to_secrets())
    cfg.settings = {
        **cfg.settings,
        "root_folder_id": root_id,
        "root_folder_name": DRIVE_ROOT_NAME,
        "scope": tokens.scope,
    }
    cfg.status = IntegrationStatus.connected
    cfg.last_error = None
    cfg.last_checked_at = utcnow()
    audit.record(
        db,
        "integration.drive_connected",
        tenant_id=tenant_id,
        actor_type="user",
        actor_id=user_id,
        details={"scope": tokens.scope},
    )
    db.commit()
    return _redirect("connected", tenant_id)


@router.post(
    "/tenants/{tenant_id}/integrations/fake-drive", response_model=IntegrationOut, status_code=201
)
def connect_fake_drive(ctx: Admin, db: DB) -> IntegrationOut:
    """Development only: in-memory FAKE storage. Refused when ALLOW_FAKE_PROVIDERS=false."""
    if not get_settings().allow_fake_providers:
        raise NotFound()
    if registry.drive_config(db, ctx.tenant_id):
        raise Conflict("A storage integration already exists")
    cfg = IntegrationConfig(
        tenant_id=ctx.tenant_id,
        kind=IntegrationKind.google_drive,
        provider="fake",
        name="FAKE storage (dev only)",
        status=IntegrationStatus.connected,
        settings={},
    )
    db.add(cfg)
    db.commit()
    return _out(cfg)
