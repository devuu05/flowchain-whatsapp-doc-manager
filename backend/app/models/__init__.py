"""Import all models so Alembic and the mapper registry see them."""

from app.models.documents import Document, DocumentStatus, MediaBlob, PendingMedia, PendingStatus
from app.models.integrations import (
    AuthorizedSender,
    Category,
    IntegrationConfig,
    IntegrationKind,
    IntegrationStatus,
)
from app.models.messaging import (
    Direction,
    InboundEvent,
    InboundStatus,
    Message,
    OutboundStatus,
)
from app.models.ops import AuditEvent, Job, JobStatus
from app.models.search import SearchResult, SearchSession, SearchStatus
from app.models.tenancy import (
    ROLE_RANK,
    LoginAttempt,
    Role,
    Tenant,
    TenantMembership,
    TenantStatus,
    User,
    UserSession,
)

__all__ = [
    "ROLE_RANK",
    "AuditEvent",
    "AuthorizedSender",
    "Category",
    "Direction",
    "Document",
    "DocumentStatus",
    "InboundEvent",
    "InboundStatus",
    "IntegrationConfig",
    "IntegrationKind",
    "IntegrationStatus",
    "Job",
    "JobStatus",
    "LoginAttempt",
    "MediaBlob",
    "Message",
    "OutboundStatus",
    "PendingMedia",
    "PendingStatus",
    "Role",
    "SearchResult",
    "SearchSession",
    "SearchStatus",
    "Tenant",
    "TenantMembership",
    "TenantStatus",
    "User",
    "UserSession",
]
