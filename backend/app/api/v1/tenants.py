"""Tenant administration: settings, overview, usage, members, senders, categories, audit, data."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response
from sqlalchemy import delete, func, select

from app.api.deps import DB, Admin, Auth, Member, Owner, PlatformAdmin
from app.core.config import get_settings
from app.core.errors import AppError, Conflict, Forbidden, NotFound
from app.db.base import utcnow
from app.models import (
    AuditEvent,
    AuthorizedSender,
    Category,
    Direction,
    Document,
    DocumentStatus,
    InboundEvent,
    Job,
    JobStatus,
    MediaBlob,
    Message,
    PendingMedia,
    Role,
    SearchSession,
    Tenant,
    TenantMembership,
    User,
)
from app.providers import registry
from app.providers.errors import ProviderError
from app.schemas.api import (
    AuditOut,
    CategoryIn,
    CategoryOut,
    CategoryUpdateIn,
    DeleteTenantDataIn,
    DocumentOut,
    MemberCreateIn,
    MemberOut,
    MemberUpdateIn,
    OverviewOut,
    Page,
    QuotaUpdateIn,
    SenderIn,
    SenderOut,
    TenantCreateIn,
    TenantOut,
    TenantUpdateIn,
    UsageOut,
)
from app.security.crypto import new_token
from app.security.passwords import hash_password, validate_password_strength
from app.services import audit
from app.services.auth import normalize_email, revoke_all_sessions
from app.services.tenancy import add_authorized_sender, create_tenant

router = APIRouter(prefix="/tenants", tags=["tenants"])


def _month_start() -> datetime:
    return utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


# ---- tenants ----------------------------------------------------------------------------
@router.get("", response_model=list[TenantOut])
def list_tenants(auth: Auth, db: DB) -> list[Tenant]:
    if auth.user.is_platform_admin:
        return list(db.scalars(select(Tenant).order_by(Tenant.name)))
    return list(
        db.scalars(
            select(Tenant)
            .join(TenantMembership, TenantMembership.tenant_id == Tenant.id)
            .where(TenantMembership.user_id == auth.user.id)
            .order_by(Tenant.name)
        )
    )


@router.post("", response_model=TenantOut, status_code=201)
def create(body: TenantCreateIn, admin: PlatformAdmin, db: DB) -> Tenant:
    owner = None
    if body.owner_email:
        owner = db.scalar(select(User).where(User.email == normalize_email(body.owner_email)))
        if owner is None:
            raise NotFound("Owner user not found; create the user first")
    tenant = create_tenant(db, body.name, owner)
    audit.record(
        db,
        "tenant.created",
        tenant_id=tenant.id,
        actor_type="user",
        actor_id=admin.user.id,
        ip=admin.ip,
    )
    db.commit()
    return tenant


@router.get("/{tenant_id}", response_model=TenantOut)
def get_tenant(ctx: Member) -> Tenant:
    return ctx.tenant


@router.patch("/{tenant_id}", response_model=TenantOut)
def update_tenant(body: TenantUpdateIn, ctx: Admin, db: DB) -> Tenant:
    t = db.merge(ctx.tenant)
    changes = body.model_dump(exclude_unset=True)
    if "ai_enabled" in changes and ctx.role != Role.owner:
        raise Forbidden("Only the workspace owner can change AI processing consent")
    if changes.get("ai_enabled") and not (changes.get("ai_consent_note") or t.ai_consent_note):
        raise AppError(
            "Record who authorized AI processing (ai_consent_note) before enabling it",
            code="consent_required",
        )
    for k, v in changes.items():
        setattr(t, k, v)
    audit.record(
        db,
        "tenant.updated",
        tenant_id=t.id,
        actor_type="user",
        actor_id=ctx.user.id,
        ip=ctx.auth.ip,
        details={"fields": sorted(changes)},
    )
    db.commit()
    return t


@router.patch("/{tenant_id}/quotas", response_model=TenantOut)
def update_quotas(
    tenant_id: uuid.UUID, body: QuotaUpdateIn, admin: PlatformAdmin, db: DB
) -> Tenant:
    t = db.get(Tenant, tenant_id)
    if t is None:
        raise NotFound()
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(t, k, v)
    audit.record(
        db,
        "tenant.quotas_updated",
        tenant_id=t.id,
        actor_type="user",
        actor_id=admin.user.id,
        ip=admin.ip,
        details=changes,
    )
    db.commit()
    return t


@router.get("/{tenant_id}/overview", response_model=OverviewOut)
def overview(ctx: Member, db: DB) -> OverviewOut:
    tid = ctx.tenant_id
    counts = dict(
        db.execute(
            select(Document.status, func.count())
            .where(Document.tenant_id == tid)
            .group_by(Document.status)
        ).all()
    )
    by_type = dict(
        db.execute(
            select(Document.document_type, func.count())
            .where(Document.tenant_id == tid, Document.status == DocumentStatus.stored)
            .group_by(Document.document_type)
        ).all()
    )
    jobs = dict(
        db.execute(
            select(Job.status, func.count()).where(Job.tenant_id == tid).group_by(Job.status)
        ).all()
    )
    recent = db.scalars(
        select(Document)
        .where(Document.tenant_id == tid)
        .order_by(Document.created_at.desc())
        .limit(8)
    )
    activity = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.tenant_id == tid)
        .order_by(AuditEvent.created_at.desc())
        .limit(10)
    )
    return OverviewOut(
        documents_total=sum(counts.values()),
        documents_this_month=db.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.tenant_id == tid, Document.created_at >= _month_start())
        )
        or 0,
        stored=counts.get(DocumentStatus.stored, 0),
        failed=counts.get(DocumentStatus.failed, 0) + counts.get(DocumentStatus.rejected, 0),
        awaiting_description=counts.get(DocumentStatus.awaiting_description, 0),
        review_required=db.scalar(
            select(func.count())
            .select_from(Document)
            .where(
                Document.tenant_id == tid,
                Document.review_required.is_(True),
                Document.status == DocumentStatus.stored,
            )
        )
        or 0,
        jobs_queued=jobs.get(JobStatus.queued, 0) + jobs.get(JobStatus.running, 0),
        jobs_dead=jobs.get(JobStatus.dead, 0),
        by_type={(k or "Unclassified"): v for k, v in by_type.items()},
        recent_documents=[DocumentOut.model_validate(d) for d in recent],
        recent_activity=[AuditOut.model_validate(a) for a in activity],
    )


@router.get("/{tenant_id}/usage", response_model=UsageOut)
def usage(ctx: Member, db: DB) -> UsageOut:
    tid, start = ctx.tenant_id, _month_start()
    docs = (
        select(Document).where(Document.tenant_id == tid, Document.created_at >= start).subquery()
    )
    return UsageOut(
        month_start=start,
        documents_this_month=db.scalar(select(func.count()).select_from(docs)) or 0,
        monthly_document_quota=ctx.tenant.monthly_document_quota,
        ai_classifications_this_month=db.scalar(
            select(func.count()).select_from(docs).where(docs.c.classifier == "gemini")
        )
        or 0,
        monthly_ai_quota=ctx.tenant.monthly_ai_quota,
        messages_sent_this_month=db.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.tenant_id == tid,
                Message.direction == Direction.outbound,
                Message.status == "sent",
                Message.created_at >= start,
            )
        )
        or 0,
        storage_bytes_this_month=db.scalar(
            select(func.coalesce(func.sum(docs.c.size_bytes), 0)).where(
                docs.c.status == DocumentStatus.stored
            )
        )
        or 0,
        transient_blob_bytes=db.scalar(
            select(func.coalesce(func.sum(MediaBlob.size_bytes), 0)).where(
                MediaBlob.tenant_id == tid
            )
        )
        or 0,
        ai_enabled=ctx.tenant.ai_enabled,
        ai_provider_configured=bool(get_settings().gemini_api_key.get_secret_value()),
    )


# ---- members ----------------------------------------------------------------------------
def _member_out(m: TenantMembership, u: User) -> MemberOut:
    return MemberOut(
        user_id=u.id,
        email=u.email,
        full_name=u.full_name,
        role=m.role,
        is_active=u.is_active,
        last_login_at=u.last_login_at,
    )


@router.get("/{tenant_id}/members", response_model=list[MemberOut])
def list_members(ctx: Admin, db: DB) -> list[MemberOut]:
    rows = db.execute(
        select(TenantMembership, User)
        .join(User, User.id == TenantMembership.user_id)
        .where(TenantMembership.tenant_id == ctx.tenant_id)
        .order_by(User.email)
    ).all()
    return [_member_out(m, u) for m, u in rows]


@router.post("/{tenant_id}/members", response_model=MemberOut, status_code=201)
def add_member(body: MemberCreateIn, ctx: Admin, db: DB) -> MemberOut:
    if body.role == Role.owner and ctx.role != Role.owner:
        raise Forbidden("Only owners can add owners")
    email = normalize_email(body.email)
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        if not body.initial_password:
            raise AppError("initial_password is required for a new user", code="password_required")
        if problem := validate_password_strength(body.initial_password):
            raise AppError(problem, code="weak_password")
        user = User(
            email=email,
            full_name=body.full_name,
            password_hash=hash_password(body.initial_password),
        )
        db.add(user)
        db.flush()
    existing = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == ctx.tenant_id, TenantMembership.user_id == user.id
        )
    )
    if existing:
        raise Conflict("User is already a member")
    m = TenantMembership(tenant_id=ctx.tenant_id, user_id=user.id, role=body.role)
    db.add(m)
    audit.record(
        db,
        "member.added",
        tenant_id=ctx.tenant_id,
        actor_type="user",
        actor_id=ctx.user.id,
        target_type="user",
        target_id=user.id,
        ip=ctx.auth.ip,
        details={"role": body.role},
    )
    db.commit()
    return _member_out(m, user)


def _owner_count(db: DB, tenant_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(TenantMembership)
            .where(TenantMembership.tenant_id == tenant_id, TenantMembership.role == Role.owner)
        )
        or 0
    )


@router.patch("/{tenant_id}/members/{user_id}", response_model=MemberOut)
def update_member(user_id: uuid.UUID, body: MemberUpdateIn, ctx: Owner, db: DB) -> MemberOut:
    m = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == ctx.tenant_id, TenantMembership.user_id == user_id
        )
    )
    if m is None:
        raise NotFound()
    if m.role == Role.owner and body.role != Role.owner and _owner_count(db, ctx.tenant_id) <= 1:
        raise Conflict("A workspace must keep at least one owner")
    m.role = body.role
    audit.record(
        db,
        "member.role_changed",
        tenant_id=ctx.tenant_id,
        actor_type="user",
        actor_id=ctx.user.id,
        target_type="user",
        target_id=user_id,
        ip=ctx.auth.ip,
        details={"role": body.role},
    )
    db.commit()
    user = db.get(User, user_id)
    assert user is not None
    return _member_out(m, user)


@router.delete("/{tenant_id}/members/{user_id}", status_code=204)
def remove_member(user_id: uuid.UUID, ctx: Admin, db: DB) -> Response:
    m = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == ctx.tenant_id, TenantMembership.user_id == user_id
        )
    )
    if m is None:
        raise NotFound()
    if m.role == Role.owner:
        if ctx.role != Role.owner:
            raise Forbidden("Only owners can remove owners")
        if _owner_count(db, ctx.tenant_id) <= 1:
            raise Conflict("A workspace must keep at least one owner")
    db.delete(m)
    remaining = (
        db.scalar(
            select(func.count())
            .select_from(TenantMembership)
            .where(TenantMembership.user_id == user_id, TenantMembership.tenant_id != ctx.tenant_id)
        )
        or 0
    )
    if remaining == 0:
        revoke_all_sessions(db, user_id)
    audit.record(
        db,
        "member.removed",
        tenant_id=ctx.tenant_id,
        actor_type="user",
        actor_id=ctx.user.id,
        target_type="user",
        target_id=user_id,
        ip=ctx.auth.ip,
    )
    db.commit()
    return Response(status_code=204)


# ---- authorized senders -----------------------------------------------------------------
@router.get("/{tenant_id}/senders", response_model=list[SenderOut])
def list_senders(ctx: Member, db: DB) -> list[AuthorizedSender]:
    return list(
        db.scalars(
            select(AuthorizedSender)
            .where(AuthorizedSender.tenant_id == ctx.tenant_id)
            .order_by(AuthorizedSender.created_at)
        )
    )


@router.post("/{tenant_id}/senders", response_model=SenderOut, status_code=201)
def add_sender(body: SenderIn, ctx: Admin, db: DB) -> AuthorizedSender:
    try:
        sender = add_authorized_sender(db, ctx.tenant_id, body.phone, body.display_name)
    except ValueError as exc:
        raise AppError(str(exc), code="invalid_phone") from exc
    audit.record(
        db,
        "sender.authorized",
        tenant_id=ctx.tenant_id,
        actor_type="user",
        actor_id=ctx.user.id,
        target_type="sender",
        target_id=sender.id,
        ip=ctx.auth.ip,
    )
    db.commit()
    return sender


@router.delete("/{tenant_id}/senders/{sender_id}", status_code=204)
def remove_sender(sender_id: uuid.UUID, ctx: Admin, db: DB) -> Response:
    s = db.scalar(
        select(AuthorizedSender).where(
            AuthorizedSender.id == sender_id, AuthorizedSender.tenant_id == ctx.tenant_id
        )
    )
    if s is None:
        raise NotFound()
    s.is_active = False
    audit.record(
        db,
        "sender.revoked",
        tenant_id=ctx.tenant_id,
        actor_type="user",
        actor_id=ctx.user.id,
        target_type="sender",
        target_id=s.id,
        ip=ctx.auth.ip,
    )
    db.commit()
    return Response(status_code=204)


# ---- categories -------------------------------------------------------------------------
@router.get("/{tenant_id}/categories", response_model=list[CategoryOut])
def list_categories(ctx: Member, db: DB) -> list[Category]:
    return list(
        db.scalars(
            select(Category).where(Category.tenant_id == ctx.tenant_id).order_by(Category.name)
        )
    )


@router.post("/{tenant_id}/categories", response_model=CategoryOut, status_code=201)
def add_category(body: CategoryIn, ctx: Admin, db: DB) -> Category:
    exists = db.scalar(
        select(Category).where(
            Category.tenant_id == ctx.tenant_id, func.lower(Category.name) == body.name.lower()
        )
    )
    if exists:
        raise Conflict("Category already exists")
    c = Category(
        tenant_id=ctx.tenant_id,
        name=body.name.strip(),
        folder_name=(body.folder_name or body.name).strip().replace("/", "-"),
        ai_allowed=body.ai_allowed,
        keywords=[k.strip().lower()[:40] for k in body.keywords if k.strip()],
    )
    db.add(c)
    audit.record(
        db,
        "category.created",
        tenant_id=ctx.tenant_id,
        actor_type="user",
        actor_id=ctx.user.id,
        ip=ctx.auth.ip,
        details={"name": c.name},
    )
    db.commit()
    return c


@router.patch("/{tenant_id}/categories/{category_id}", response_model=CategoryOut)
def update_category(category_id: uuid.UUID, body: CategoryUpdateIn, ctx: Admin, db: DB) -> Category:
    c = db.scalar(
        select(Category).where(Category.id == category_id, Category.tenant_id == ctx.tenant_id)
    )
    if c is None:
        raise NotFound()
    changes = body.model_dump(exclude_unset=True)
    if c.name == "Other" and changes.get("is_active") is False:
        raise Conflict("The Other category cannot be disabled")
    if changes.get("folder_name"):
        changes["folder_name"] = changes["folder_name"].replace("/", "-")
    for k, v in changes.items():
        setattr(c, k, v)
    audit.record(
        db,
        "category.updated",
        tenant_id=ctx.tenant_id,
        actor_type="user",
        actor_id=ctx.user.id,
        ip=ctx.auth.ip,
        details={"name": c.name, **changes},
    )
    db.commit()
    return c


# ---- audit ------------------------------------------------------------------------------
@router.get("/{tenant_id}/audit", response_model=Page[AuditOut])
def list_audit(
    ctx: Admin,
    db: DB,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    action: Annotated[str | None, Query(max_length=80)] = None,
) -> Page[AuditOut]:
    q = select(AuditEvent).where(AuditEvent.tenant_id == ctx.tenant_id)
    if action:
        q = q.where(AuditEvent.action.startswith(action))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.order_by(AuditEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return Page(
        items=[AuditOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---- data export / deletion -------------------------------------------------------------
@router.get("/{tenant_id}/export")
def export_data(ctx: Owner, db: DB) -> Response:
    """Metadata export (JSON). Original files remain in the tenant's own Google Drive."""
    import json

    tid = ctx.tenant_id

    def rows(model: Any, *exclude: str) -> list[dict[str, Any]]:
        out = []
        for r in db.scalars(select(model).where(model.tenant_id == tid)):
            out.append(
                {
                    c.name: getattr(r, c.name)
                    for c in model.__table__.columns
                    if c.name not in exclude
                }
            )
        return out

    payload = {
        "exported_at": utcnow().isoformat(),
        "tenant": {c.name: getattr(ctx.tenant, c.name) for c in Tenant.__table__.columns},
        "documents": rows(Document),
        "categories": rows(Category),
        "authorized_senders": rows(AuthorizedSender),
        "messages": rows(Message),
        "audit_events": rows(AuditEvent),
        "note": "Integration secrets are never exported. Original files are in Google Drive.",
    }
    audit.record(
        db,
        "tenant.exported",
        tenant_id=tid,
        actor_type="user",
        actor_id=ctx.user.id,
        ip=ctx.auth.ip,
    )
    db.commit()
    body = json.dumps(payload, default=str, indent=1)
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="flowchain-export-{ctx.tenant.slug}.json"'
        },
    )


@router.post("/{tenant_id}/delete-data")
def delete_data(body: DeleteTenantDataIn, ctx: Owner, db: DB) -> dict[str, Any]:
    """Irreversibly delete all document metadata, conversations and queued work for the tenant.

    Optionally moves the app-created Drive files to the Drive trash. Audit events are kept
    (they contain no document content) so the deletion itself remains accountable.
    """
    tid = ctx.tenant_id
    if body.confirm_slug != ctx.tenant.slug:
        raise AppError(
            "Confirmation does not match the workspace slug", code="confirmation_mismatch"
        )
    trashed, trash_errors = 0, 0
    if body.trash_drive_files:
        try:
            storage, _ = registry.storage_for(db, tid)
            for fid in db.scalars(
                select(Document.storage_file_id).where(
                    Document.tenant_id == tid, Document.storage_file_id.is_not(None)
                )
            ):
                try:
                    storage.trash(str(fid))
                    trashed += 1
                except ProviderError:
                    trash_errors += 1
        except ProviderError as exc:
            raise AppError(f"Drive unavailable: {exc}", code="storage_unavailable") from exc
    counts: dict[str, int] = {}
    for model in (Job, PendingMedia, SearchSession, Message, Document, MediaBlob, InboundEvent):
        counts[model.__tablename__] = db.execute(
            delete(model).where(model.tenant_id == tid)
        ).rowcount  # type: ignore[attr-defined]
    audit.record(
        db,
        "tenant.data_deleted",
        tenant_id=tid,
        actor_type="user",
        actor_id=ctx.user.id,
        ip=ctx.auth.ip,
        details={
            "counts": counts,
            "drive_trashed": trashed,
            "drive_errors": trash_errors,
            "ref": new_token(6),
        },
    )
    db.commit()
    return {"deleted": counts, "drive_trashed": trashed, "drive_errors": trash_errors}
