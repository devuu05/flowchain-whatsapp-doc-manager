"""Request/response schemas for the dashboard API. Secrets never appear in responses."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field

T = TypeVar("T")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


# ---- auth -------------------------------------------------------------------------------
class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class MembershipOut(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    role: str


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_platform_admin: bool
    csrf_token: str
    memberships: list[MembershipOut]


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


# ---- tenants ----------------------------------------------------------------------------
class TenantOut(ORM):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    timezone: str
    ai_enabled: bool
    ai_consent_note: str | None
    max_file_bytes: int | None
    monthly_document_quota: int
    monthly_ai_quota: int
    created_at: datetime


class TenantCreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    owner_email: EmailStr | None = None


class TenantUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    timezone: str | None = Field(default=None, max_length=64)
    ai_enabled: bool | None = None
    ai_consent_note: str | None = Field(default=None, max_length=500)
    max_file_bytes: int | None = Field(default=None, ge=1024, le=100 * 1024 * 1024)


class QuotaUpdateIn(BaseModel):
    monthly_document_quota: int | None = Field(default=None, ge=0, le=1_000_000)
    monthly_ai_quota: int | None = Field(default=None, ge=0, le=1_000_000)
    status: Literal["active", "suspended"] | None = None


# ---- members ----------------------------------------------------------------------------
class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    last_login_at: datetime | None


class MemberCreateIn(BaseModel):
    email: EmailStr
    full_name: str = Field(default="", max_length=200)
    role: Literal["owner", "admin", "member"] = "member"
    initial_password: str | None = Field(default=None, min_length=12, max_length=256)


class MemberUpdateIn(BaseModel):
    role: Literal["owner", "admin", "member"]


# ---- senders / categories ---------------------------------------------------------------
class SenderOut(ORM):
    id: uuid.UUID
    phone: str
    display_name: str
    is_active: bool
    created_at: datetime


class SenderIn(BaseModel):
    phone: str = Field(min_length=8, max_length=24)
    display_name: str = Field(default="", max_length=120)


class CategoryOut(ORM):
    id: uuid.UUID
    name: str
    folder_name: str
    is_active: bool
    ai_allowed: bool
    keywords: list[str]


class CategoryIn(BaseModel):
    name: str = Field(min_length=2, max_length=60, pattern=r"^[A-Za-z0-9 /&\-]+$")
    folder_name: str | None = Field(default=None, max_length=120)
    ai_allowed: bool = True
    keywords: list[str] = Field(default_factory=list, max_length=30)


class CategoryUpdateIn(BaseModel):
    folder_name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None
    ai_allowed: bool | None = None
    keywords: list[str] | None = Field(default=None, max_length=30)


# ---- documents --------------------------------------------------------------------------
class DocumentOut(ORM):
    id: uuid.UUID
    status: str
    original_filename: str
    standardized_filename: str | None
    extension: str
    mime_type: str
    size_bytes: int
    document_type: str | None
    company_or_person: str | None
    date_or_period: str | None
    doc_date: date | None
    folder: str | None
    review_required: bool
    confidence: float | None
    sender: str | None
    created_at: datetime
    stored_at: datetime | None
    failure_reason: str | None


class DocumentDetailOut(DocumentOut):
    description: str | None
    summary: str | None
    classifier: str | None
    extraction_status: str | None
    extraction_warnings: list[str]
    page_count: int | None
    char_count: int | None
    storage_provider: str | None
    storage_file_id: str | None
    sha256: str
    jobs: list[JobOut] = []


class DocumentUpdateIn(BaseModel):
    document_type: str | None = Field(default=None, max_length=60)
    company_or_person: str | None = Field(default=None, max_length=200)
    date_or_period: str | None = Field(default=None, max_length=60)
    review_required: bool | None = None


# ---- jobs / audit -----------------------------------------------------------------------
class JobOut(ORM):
    id: uuid.UUID
    kind: str
    status: str
    attempts: int
    max_attempts: int
    run_at: datetime
    last_error: str | None
    error_category: str | None
    document_id: uuid.UUID | None
    created_at: datetime
    finished_at: datetime | None


class AuditOut(ORM):
    id: uuid.UUID
    actor_type: str
    actor_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    ip: str | None
    details: dict[str, Any]
    created_at: datetime


# ---- integrations -----------------------------------------------------------------------
class IntegrationOut(ORM):
    id: uuid.UUID
    kind: str
    provider: str
    name: str
    status: str
    settings: dict[str, Any]
    last_error: str | None
    last_checked_at: datetime | None
    created_at: datetime
    has_secrets: bool = False
    webhook_url: str | None = None


class WhatsAppConnectIn(BaseModel):
    provider: Literal["wasender", "fake"] = "wasender"
    name: str = Field(default="WhatsApp", max_length=120)
    phone_number: str = Field(min_length=8, max_length=24)
    api_key: str | None = Field(default=None, min_length=8, max_length=500)
    webhook_secret: str | None = Field(default=None, min_length=8, max_length=500)


class OAuthStartOut(BaseModel):
    authorization_url: str


class OverviewOut(BaseModel):
    documents_total: int
    documents_this_month: int
    stored: int
    failed: int
    awaiting_description: int
    review_required: int
    jobs_queued: int
    jobs_dead: int
    by_type: dict[str, int]
    recent_documents: list[DocumentOut]
    recent_activity: list[AuditOut]


class UsageOut(BaseModel):
    month_start: datetime
    documents_this_month: int
    monthly_document_quota: int
    ai_classifications_this_month: int
    monthly_ai_quota: int
    messages_sent_this_month: int
    storage_bytes_this_month: int
    transient_blob_bytes: int
    ai_enabled: bool
    ai_provider_configured: bool


class DeleteTenantDataIn(BaseModel):
    confirm_slug: str
    trash_drive_files: bool = False


DocumentDetailOut.model_rebuild()
