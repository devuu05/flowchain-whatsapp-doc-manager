"""WhatsApp, Google Drive and OneDrive connection management. Secrets are write-only."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import DB, Admin, Member
from app.core.config import get_settings
from app.core.errors import AppError, Conflict, NotAuthenticated, NotFound
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
from app.providers.storage import google_oauth, microsoft_oauth
from app.providers.storage.google_drive import GoogleDriveStorage
from app.providers.storage.onedrive import OneDriveStorage
from app.schemas.api import IntegrationOut, OAuthStartOut, WhatsAppConnectIn
from app.security.crypto import (
    constant_time_equals,
    decrypt_json,
    encrypt_json,
    hmac_hex,
    new_token,
)
from app.security.signed_urls import sign, verify
from app.services import audit
from app.services.auth import resolve_session
from app.services.tenancy import normalize_phone

router = APIRouter(tags=["integrations"])
log = logging.getLogger(__name__)
DRIVE_ROOT_NAME = "Flow Chain Documents"


def _out(cfg: IntegrationConfig) -> IntegrationOut:
    out = IntegrationOut.model_validate(cfg)
    out.has_secrets = bool(cfg.encrypted_secrets)
    if cfg.kind == IntegrationKind.whatsapp and cfg.connection_key:
        out.webhook_url = (
            f"{get_settings().public_base_url}/api/v1/webhooks/{cfg.provider}/{cfg.connection_key}"
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
    out = [_out(r) for r in rows]
    if ctx.role == Role.member:
        for o in out:
            o.webhook_url = None  # contains the connection key; admins only
    return out


@router.post(
    "/tenants/{tenant_id}/integrations/whatsapp", response_model=IntegrationOut, status_code=201
)
def connect_whatsapp(body: WhatsAppConnectIn, ctx: Admin, db: DB) -> IntegrationOut:
    s = get_settings()
    if body.provider == "fake" and not s.allow_fake_providers:
        raise AppError("Fake provider is disabled", code="provider_unavailable")
    if body.provider in ("wasender", "wasender_dev") and not (body.api_key and body.webhook_secret):
        raise AppError(
            "The API token and webhook signing secret are both required", code="secrets_required"
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
def _session_binding(session_token_hash: str) -> str:
    return hmac_hex("oauth-session", session_token_hash)[:32]


@router.post("/tenants/{tenant_id}/integrations/google/start", response_model=OAuthStartOut)
def google_start(ctx: Admin) -> OAuthStartOut:
    s = get_settings()
    if not s.google_client_id or not s.google_client_secret.get_secret_value():
        raise AppError("Google OAuth is not configured on the server", code="oauth_unconfigured")
    state = sign(
        "google_oauth",
        {
            "t": str(ctx.tenant_id),
            "u": str(ctx.user.id),
            "s": _session_binding(ctx.auth.session.token_hash),
        },
        600,
    )
    return OAuthStartOut(authorization_url=google_oauth.build_authorization_url(state))


def _redirect(
    status: str, tenant_id: uuid.UUID | None = None, provider: str = "google"
) -> RedirectResponse:
    base = get_settings().dashboard_url
    if tenant_id is None:
        return RedirectResponse(f"{base}/app?drive={status}", status_code=303)
    suffix = "" if provider == "google" else f"&provider={provider}"
    return RedirectResponse(
        f"{base}/t/{tenant_id}/settings/drive?result={status}{suffix}", status_code=303
    )


class _CallbackRejected(Exception):
    def __init__(self, status: str, tenant_id: uuid.UUID | None = None) -> None:
        super().__init__(status)
        self.status = status
        self.tenant_id = tenant_id


def _callback_admin(
    request: Request, db: DB, state_kind: str, state: str
) -> tuple[uuid.UUID, uuid.UUID]:
    """Validate an OAuth callback (signed state, same browser session, admin role).

    Returns (tenant_id, user_id) or raises _CallbackRejected with the redirect status.
    """
    data = verify(state_kind, state)
    if data is None:
        raise _CallbackRejected("invalid_state")
    tenant_id, user_id = uuid.UUID(data["t"]), uuid.UUID(data["u"])
    # The flow must finish in the same signed-in browser session that started it, so a
    # consent link cannot be handed to someone else to attach their storage to this tenant.
    try:
        _, sess = resolve_session(db, request.cookies.get(get_settings().session_cookie_name))
    except NotAuthenticated as exc:
        raise _CallbackRejected("session_mismatch", tenant_id) from exc
    if sess.user_id != user_id or not constant_time_equals(
        str(data.get("s", "")), _session_binding(sess.token_hash)
    ):
        raise _CallbackRejected("session_mismatch", tenant_id)
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
        raise _CallbackRejected("forbidden", tenant_id)
    return tenant_id, user_id


@router.get("/integrations/google/callback", include_in_schema=False)
def google_callback(
    request: Request,
    db: DB,
    state: str = Query(max_length=1000),
    code: str | None = Query(default=None, max_length=2000),
    error: str | None = Query(default=None, max_length=200),
) -> RedirectResponse:
    try:
        tenant_id, user_id = _callback_admin(request, db, "google_oauth", state)
    except _CallbackRejected as rej:
        return _redirect(rej.status, rej.tenant_id)
    if error or not code:
        return _redirect("denied", tenant_id)
    cfg = registry.drive_config(db, tenant_id)
    if cfg is not None and cfg.provider == "onedrive":
        return _redirect("other_storage", tenant_id)
    try:
        tokens = google_oauth.exchange_code(code)
        if not tokens.refresh_token:
            return _redirect("no_refresh_token", tenant_id)
        root_id = cfg.settings.get("root_folder_id") if cfg else None
        # Reuse the previous root only if the newly authorized account can reach it
        # (with drive.file a different Google account cannot see the old folder).
        if root_id and not GoogleDriveStorage.folder_accessible(tokens.access_token, root_id):
            root_id = None
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


# ---- Microsoft OneDrive OAuth ------------------------------------------------------------
@router.post("/tenants/{tenant_id}/integrations/microsoft/start", response_model=OAuthStartOut)
def microsoft_start(ctx: Admin, db: DB) -> OAuthStartOut:
    s = get_settings()
    if not s.microsoft_client_id or not s.microsoft_client_secret.get_secret_value():
        raise AppError("Microsoft OAuth is not configured on the server", code="oauth_unconfigured")
    existing = registry.drive_config(db, ctx.tenant_id)
    if existing is not None and existing.provider not in ("onedrive",):
        raise Conflict("Disconnect the current storage integration before connecting OneDrive")
    state = sign(
        "microsoft_oauth",
        {
            "t": str(ctx.tenant_id),
            "u": str(ctx.user.id),
            "s": _session_binding(ctx.auth.session.token_hash),
        },
        600,
    )
    return OAuthStartOut(authorization_url=microsoft_oauth.build_authorization_url(state))


@router.get("/integrations/microsoft/callback", include_in_schema=False)
def microsoft_callback(
    request: Request,
    db: DB,
    state: str = Query(max_length=1000),
    code: str | None = Query(default=None, max_length=4000),
    error: str | None = Query(default=None, max_length=200),
) -> RedirectResponse:
    try:
        tenant_id, user_id = _callback_admin(request, db, "microsoft_oauth", state)
    except _CallbackRejected as rej:
        return _redirect(rej.status, rej.tenant_id, "onedrive")
    if error or not code:
        return _redirect("denied", tenant_id, "onedrive")
    cfg = registry.drive_config(db, tenant_id)
    if cfg is not None and cfg.provider != "onedrive":
        return _redirect("other_storage", tenant_id, "onedrive")
    try:
        tokens = microsoft_oauth.exchange_code(code)
        if not tokens.refresh_token:
            return _redirect("no_refresh_token", tenant_id, "onedrive")
        root_id = cfg.settings.get("root_folder_id") if cfg else None
        # Reuse the previous root only if the newly authorized account can reach it.
        if root_id and not OneDriveStorage.folder_accessible(tokens.access_token, root_id):
            root_id = None
        if not root_id:
            root_id = OneDriveStorage.create_root_folder(tokens.access_token, DRIVE_ROOT_NAME)
        account = OneDriveStorage.account_label(tokens.access_token)
    except ProviderError as exc:
        log.warning("microsoft_oauth_failed", extra={"error": str(exc)})
        return _redirect("error", tenant_id, "onedrive")
    if cfg is None:
        cfg = IntegrationConfig(
            tenant_id=tenant_id,
            kind=IntegrationKind.onedrive,
            provider="onedrive",
            name="OneDrive",
            settings={},
        )
        db.add(cfg)
    cfg.encrypted_secrets = encrypt_json(tokens.to_secrets())
    cfg.settings = {
        **cfg.settings,
        "root_folder_id": root_id,
        "root_folder_name": DRIVE_ROOT_NAME,
        "scope": tokens.scope,
        "account": account,
    }
    cfg.status = IntegrationStatus.connected
    cfg.last_error = None
    cfg.last_checked_at = utcnow()
    audit.record(
        db,
        "integration.onedrive_connected",
        tenant_id=tenant_id,
        actor_type="user",
        actor_id=user_id,
        details={"scope": tokens.scope},
    )
    db.commit()
    return _redirect("connected", tenant_id, "onedrive")


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
