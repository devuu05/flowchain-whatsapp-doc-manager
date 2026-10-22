"""Shared FastAPI dependencies: DB, authentication, CSRF and tenant authorization."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Path, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Forbidden, NotFound
from app.db.session import get_db
from app.models import ROLE_RANK, Role, Tenant, TenantMembership, User, UserSession
from app.security.crypto import constant_time_equals
from app.services.auth import csrf_for, resolve_session

DB = Annotated[Session, Depends(get_db)]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def client_ip(request: Request) -> str | None:
    """Client address for audit logs and rate limits (never for authorization).

    With TRUSTED_PROXY_HOPS=N, the N-th entry from the RIGHT of X-Forwarded-For is used: those
    entries were appended by our own proxies, while entries further left are client-supplied
    and spoofable. With 0 hops the socket peer is used.
    """
    hops = get_settings().trusted_proxy_hops
    if hops > 0:
        parts = [
            p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()
        ]
        if len(parts) >= hops:
            return parts[-hops][:64]
    return request.client.host[:64] if request.client else None


@dataclass
class AuthContext:
    user: User
    session: UserSession
    ip: str | None


def get_auth(request: Request, db: DB) -> AuthContext:
    token = request.cookies.get(get_settings().session_cookie_name)
    user, sess = resolve_session(db, token)
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("x-csrf-token")
        if not constant_time_equals(sent, csrf_for(sess)):
            raise Forbidden("Missing or invalid CSRF token", code="csrf_failed")
    return AuthContext(user=user, session=sess, ip=client_ip(request))


Auth = Annotated[AuthContext, Depends(get_auth)]


def require_platform_admin(auth: Auth) -> AuthContext:
    if not auth.user.is_platform_admin:
        raise Forbidden()
    return auth


PlatformAdmin = Annotated[AuthContext, Depends(require_platform_admin)]


@dataclass
class TenantContext:
    tenant: Tenant
    role: Role
    auth: AuthContext

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.tenant.id

    @property
    def user(self) -> User:
        return self.auth.user


def _tenant_ctx(
    tenant_id: Annotated[uuid.UUID, Path()], auth: Auth, db: DB, minimum: Role
) -> TenantContext:
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == auth.user.id
        )
    )
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFound()
    if membership is None:
        if not auth.user.is_platform_admin:
            # Do not reveal whether the tenant exists.
            raise NotFound()
        role = Role.owner
    else:
        role = Role(membership.role)
    if ROLE_RANK[role] < ROLE_RANK[minimum]:
        raise Forbidden()
    return TenantContext(tenant=tenant, role=role, auth=auth)


def tenant_member(tenant_id: Annotated[uuid.UUID, Path()], auth: Auth, db: DB) -> TenantContext:
    return _tenant_ctx(tenant_id, auth, db, Role.member)


def tenant_admin(tenant_id: Annotated[uuid.UUID, Path()], auth: Auth, db: DB) -> TenantContext:
    return _tenant_ctx(tenant_id, auth, db, Role.admin)


def tenant_owner(tenant_id: Annotated[uuid.UUID, Path()], auth: Auth, db: DB) -> TenantContext:
    return _tenant_ctx(tenant_id, auth, db, Role.owner)


Member = Annotated[TenantContext, Depends(tenant_member)]
Admin = Annotated[TenantContext, Depends(tenant_admin)]
Owner = Annotated[TenantContext, Depends(tenant_owner)]
