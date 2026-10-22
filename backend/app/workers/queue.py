"""PostgreSQL-backed durable job queue (see ADR 0001)."""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utcnow
from app.models import Job, JobStatus


def enqueue(
    db: Session,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    tenant_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    run_at: datetime | None = None,
    max_attempts: int | None = None,
    document_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """Insert a job in the caller's transaction. Returns None if the idempotency key exists."""
    now = utcnow()
    stmt = (
        insert(Job)
        .values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            kind=kind,
            payload=payload or {},
            status=JobStatus.queued,
            attempts=0,
            max_attempts=max_attempts or get_settings().job_max_attempts,
            run_at=run_at or now,
            idempotency_key=idempotency_key,
            document_id=document_id,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(Job.id)
    )
    return db.execute(stmt).scalar_one_or_none()


def claim(db: Session, worker_id: str, lease_seconds: int | None = None) -> Job | None:
    """Atomically claim the next runnable job (queued & due, or running with expired lease)."""
    now = utcnow()
    lease = lease_seconds or get_settings().worker_lease_seconds
    candidate = (
        select(Job.id)
        .where(
            or_(
                and_(Job.status == JobStatus.queued, Job.run_at <= now),
                and_(Job.status == JobStatus.running, Job.locked_until < now),
            )
        )
        .order_by(Job.run_at)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    job = db.execute(
        update(Job)
        .where(Job.id == candidate)
        .values(
            status=JobStatus.running,
            locked_by=worker_id,
            locked_until=now + timedelta(seconds=lease),
            attempts=Job.attempts + 1,
            updated_at=now,
        )
        .returning(Job)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    db.commit()
    return job


class LeaseLost(Exception):
    """This worker no longer owns the job; abort before causing side effects."""


def renew_lease(job_id: uuid.UUID, worker_id: str, lease_seconds: int | None = None) -> bool:
    """Extend the lease in an independent transaction. False if another worker owns the job."""
    from app.db.session import get_sessionmaker

    lease = lease_seconds or get_settings().worker_lease_seconds
    with get_sessionmaker()() as db:
        res = db.execute(
            update(Job)
            .where(Job.id == job_id, Job.locked_by == worker_id, Job.status == JobStatus.running)
            .values(locked_until=utcnow() + timedelta(seconds=lease))
        )
        db.commit()
        return bool(res.rowcount)  # type: ignore[attr-defined]


def ensure_lease(job: Job) -> None:
    """Call immediately before an external side effect (send, upload)."""
    if not job.locked_by or not renew_lease(job.id, job.locked_by):
        raise LeaseLost(f"lease lost for job {job.id}")


def backoff_seconds(attempt: int) -> float:
    s = get_settings()
    ceiling = min(s.job_backoff_max_seconds, s.job_backoff_base_seconds * (2 ** (attempt - 1)))
    # "Equal jitter": half fixed, half random — avoids thundering herds yet keeps a floor.
    return ceiling / 2 + random.uniform(0, ceiling / 2)


def mark_succeeded(db: Session, job_id: uuid.UUID, worker_id: str) -> bool:
    now = utcnow()
    res = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.locked_by == worker_id, Job.status == JobStatus.running)
        .values(
            status=JobStatus.succeeded,
            locked_by=None,
            locked_until=None,
            finished_at=now,
            updated_at=now,
            last_error=None,
            error_category=None,
        )
    )
    return bool(res.rowcount)  # type: ignore[attr-defined]


def mark_failed(
    db: Session,
    job_id: uuid.UUID,
    worker_id: str,
    *,
    error: str,
    category: str,
    retry_after: float | None = None,
) -> str:
    """Record a failure. Returns the resulting status (queued for retry, or dead)."""
    job = db.get(Job, job_id, with_for_update=True)
    if job is None or job.locked_by != worker_id:
        return "lost_lease"
    now = utcnow()
    job.last_error = error[:4000]
    job.error_category = category
    job.locked_by = None
    job.locked_until = None
    job.updated_at = now
    if category == "permanent" or job.attempts >= job.max_attempts:
        job.status = JobStatus.dead
        job.finished_at = now
    else:
        delay = max(retry_after or 0.0, backoff_seconds(job.attempts))
        job.status = JobStatus.queued
        job.run_at = now + timedelta(seconds=delay)
    db.commit()
    return job.status


def retry_dead_job(db: Session, job: Job) -> None:
    """Operator action: requeue a dead job with a fresh attempt budget."""
    now = utcnow()
    job.status = JobStatus.queued
    job.attempts = 0
    job.run_at = now
    job.finished_at = None
    job.updated_at = now


def queue_stats(db: Session) -> dict[str, Any]:
    rows = db.execute(text("SELECT status, count(*) FROM jobs GROUP BY status")).all()
    stats: dict[str, Any] = {r[0]: r[1] for r in rows}
    oldest = db.scalar(
        select(Job.run_at)
        .where(Job.status == JobStatus.queued, Job.run_at <= utcnow())
        .order_by(Job.run_at)
        .limit(1)
    )
    stats["oldest_due_seconds"] = (utcnow() - oldest).total_seconds() if oldest else 0
    return stats
