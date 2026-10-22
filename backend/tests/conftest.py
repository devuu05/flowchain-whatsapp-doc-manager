"""Test fixtures. Integration tests run against a real local PostgreSQL database.

External HTTP (Wasender, Google, Gemini) is mocked with respx or replaced by the clearly
labelled FAKE providers — these tests do not prove the real providers work; see
docs/testing/manual-provider-tests.md for the live verification steps.
"""

from __future__ import annotations

import base64
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/flowchain_test"
    ),
)
os.environ["APP_ENV"] = "test"
os.environ["LOG_JSON"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["ALLOW_FAKE_PROVIDERS"] = "true"
os.environ["PUBLIC_BASE_URL"] = "https://api.example.test"
os.environ["INTERNAL_WEBHOOK_TOKEN"] = "internal-test-token-123"
os.environ["GEMINI_API_KEY"] = ""
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-client-secret"

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_engine, get_sessionmaker
from app.models import (
    IntegrationConfig,
    IntegrationKind,
    IntegrationStatus,
    Job,
    JobStatus,
    Role,
    Tenant,
    TenantMembership,
    User,
)
from app.providers import registry
from app.security.crypto import encrypt_json, new_token
from app.security.passwords import hash_password
from app.services import pipeline
from app.services.tenancy import add_authorized_sender, create_tenant
from app.workers import runner

PASSWORD = "Correct-Horse-9-Battery"


@pytest.fixture(scope="session", autouse=True)
def _migrate() -> Iterator[None]:
    get_settings.cache_clear()
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option(
        "script_location", os.path.join(os.path.dirname(__file__), "..", "migrations")
    )
    with get_engine().begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    command.upgrade(cfg, "head")
    runner._load_handlers()
    pipeline.ISOLATE_EXTRACTION = False
    yield


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    yield
    registry.reset_fakes()
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def db() -> Iterator[Session]:
    s = get_sessionmaker()()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    return TestClient(app)


@dataclass
class World:
    tenant: Tenant
    user: User
    conn: IntegrationConfig
    storage_cfg: IntegrationConfig
    sender: str

    @property
    def fake_wa(self) -> Any:
        return registry.fake_whatsapp(self.conn.id)

    @property
    def fake_storage(self) -> Any:
        return registry.fake_storage(self.storage_cfg.id)


def make_user(db: Session, email: str, *, admin: bool = False) -> User:
    u = User(
        email=email,
        full_name=email.split("@")[0],
        password_hash=hash_password(PASSWORD),
        is_platform_admin=admin,
    )
    db.add(u)
    db.flush()
    return u


def make_world(
    db: Session, name: str = "Acme Pilot", sender: str = "919876543210", role: Role = Role.owner
) -> World:
    user = make_user(db, f"{name.lower().replace(' ', '.')}@example.com")
    tenant = create_tenant(db, name)
    db.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role=role))
    conn = IntegrationConfig(
        tenant_id=tenant.id,
        kind=IntegrationKind.whatsapp,
        provider="fake",
        name="WA",
        status=IntegrationStatus.connected,
        connection_key=new_token(24),
        encrypted_secrets=encrypt_json({"webhook_secret": "fake-secret"}),
        settings={"phone_number": "910000000000"},
    )
    storage_cfg = IntegrationConfig(
        tenant_id=tenant.id,
        kind=IntegrationKind.google_drive,
        provider="fake",
        name="Drive",
        status=IntegrationStatus.connected,
        settings={},
    )
    db.add_all([conn, storage_cfg])
    add_authorized_sender(db, tenant.id, sender, "Pilot user")
    db.commit()
    return World(tenant=tenant, user=user, conn=conn, storage_cfg=storage_cfg, sender=sender)


@pytest.fixture
def world(db: Session) -> World:
    return make_world(db)


def login(client: TestClient, email: str, password: str = PASSWORD) -> str:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return str(r.json()["csrf_token"])


def text_event(sender: str, text: str, event_id: str | None = None) -> dict[str, Any]:
    return {
        "provider": "fake",
        "event_id": event_id or uuid.uuid4().hex,
        "event_type": "message",
        "sender": sender,
        "text": text,
    }


def media_event(
    sender: str,
    data: bytes,
    *,
    filename: str | None,
    mime: str | None,
    caption: str | None = None,
    kind: str = "document",
    event_id: str | None = None,
) -> dict[str, Any]:
    return {
        "provider": "fake",
        "event_id": event_id or uuid.uuid4().hex,
        "event_type": "message",
        "sender": sender,
        "text": caption,
        "media": {
            "kind": kind,
            "mime_type": mime,
            "filename": filename,
            "size_bytes": len(data),
            "provider_ref": {"b64": base64.b64encode(data).decode()},
        },
    }


def post_events(client: TestClient, world: World, *events: dict[str, Any]) -> dict[str, Any]:
    r = client.post(
        f"/api/v1/webhooks/fake/{world.conn.connection_key}",
        json={"events": list(events)},
        headers={"x-webhook-signature": "fake-secret"},
    )
    assert r.status_code == 200, r.text
    return dict(r.json())


def drain(max_jobs: int = 200, *, include_future: bool = False) -> int:
    """Run queued jobs synchronously until none are due. Returns number processed."""
    n = 0
    while n < max_jobs:
        if include_future:
            with get_engine().begin() as conn:
                conn.execute(text("UPDATE jobs SET run_at = now() WHERE status = 'queued'"))
        if not runner.run_one("test-worker"):
            break
        n += 1
    return n


def jobs_by_status(db: Session) -> dict[str, int]:
    db.expire_all()
    out: dict[str, int] = {}
    for j in db.query(Job).all():
        out[j.status] = out.get(j.status, 0) + 1
    return out


def sent_texts(world: World) -> list[str]:
    return [m["text"] for m in world.fake_wa.sent if m["type"] == "text"]


__all__ = [
    "PASSWORD",
    "JobStatus",
    "World",
    "drain",
    "login",
    "make_user",
    "make_world",
    "media_event",
    "post_events",
    "sent_texts",
    "text_event",
]
