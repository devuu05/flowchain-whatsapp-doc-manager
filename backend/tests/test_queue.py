"""Durable Postgres queue: idempotency, retries, dead-lettering, lease recovery, concurrency."""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.session import get_engine
from app.models import Job, JobStatus
from app.workers import queue, runner
from app.workers.errors import PermanentJobError, TransientJobError
from app.workers.registry import HANDLERS, JobSpec

CALLS: list[str] = []


@pytest.fixture(autouse=True)
def _handlers() -> None:
    CALLS.clear()

    def ok(db: Session, job: Job) -> None:
        CALLS.append(job.payload.get("v", ""))

    def transient(db: Session, job: Job) -> None:
        CALLS.append("t")
        raise TransientJobError("provider timeout")

    def permanent(db: Session, job: Job) -> None:
        raise PermanentJobError("bad input")

    def flaky(db: Session, job: Job) -> None:
        CALLS.append("f")
        if job.attempts < 3:
            raise TransientJobError("try again")

    def crash(db: Session, job: Job) -> None:
        raise RuntimeError("unexpected bug")

    def dbfail(db: Session, job: Job) -> None:
        raise OperationalError("SELECT 1", {}, Exception("connection reset"))

    dead_marks: list[str] = []

    def on_dead(db: Session, job: Job) -> None:
        CALLS.append(f"dead:{job.kind}")

    HANDLERS.update(
        {
            "t_ok": JobSpec(ok),
            "t_transient": JobSpec(transient, on_dead),
            "t_perm": JobSpec(permanent, on_dead),
            "t_flaky": JobSpec(flaky),
            "t_crash": JobSpec(crash),
            "t_dbfail": JobSpec(dbfail),
        }
    )
    del dead_marks


def _job(db: Session, jid: uuid.UUID | None) -> Job:
    assert jid is not None
    db.expire_all()
    job = db.get(Job, jid)
    assert job is not None
    return job


def _due(db: Session) -> None:
    db.execute(update(Job).values(run_at=utcnow() - timedelta(seconds=1)))
    db.commit()


def test_enqueue_is_idempotent(db: Session) -> None:
    a = queue.enqueue(db, "t_ok", {"v": "1"}, idempotency_key="k1")
    b = queue.enqueue(db, "t_ok", {"v": "2"}, idempotency_key="k1")
    db.commit()
    assert a is not None and b is None
    assert db.query(Job).count() == 1


def test_success_path(db: Session) -> None:
    jid = queue.enqueue(db, "t_ok", {"v": "x"})
    db.commit()
    assert runner.run_one("w1")
    job = _job(db, jid)
    assert job.status == JobStatus.succeeded and job.attempts == 1 and job.finished_at
    assert not runner.run_one("w1")


def test_transient_retries_with_backoff_then_dead(db: Session) -> None:
    jid = queue.enqueue(db, "t_transient", max_attempts=3)
    db.commit()
    runner.run_one("w")
    job = _job(db, jid)
    assert job.status == JobStatus.queued and job.attempts == 1
    assert job.run_at > utcnow() and job.error_category == "transient"
    assert not runner.run_one("w")  # not due yet
    for _ in range(2):
        _due(db)
        runner.run_one("w")
    job = _job(db, jid)
    assert job.status == JobStatus.dead and job.attempts == 3
    assert "provider timeout" in (job.last_error or "")
    assert CALLS.count("t") == 3 and "dead:t_transient" in CALLS


def test_flaky_job_eventually_succeeds(db: Session) -> None:
    jid = queue.enqueue(db, "t_flaky")
    db.commit()
    for _ in range(3):
        _due(db)
        runner.run_one("w")
    assert _job(db, jid).status == JobStatus.succeeded


def test_permanent_error_goes_dead_immediately(db: Session) -> None:
    jid = queue.enqueue(db, "t_perm")
    db.commit()
    runner.run_one("w")
    job = _job(db, jid)
    assert job.status == JobStatus.dead and job.attempts == 1 and job.error_category == "permanent"
    assert "dead:t_perm" in CALLS


def test_unexpected_exception_is_retried(db: Session) -> None:
    jid = queue.enqueue(db, "t_crash")
    db.commit()
    runner.run_one("w")
    job = _job(db, jid)
    assert job.status == JobStatus.queued and "RuntimeError" in (job.last_error or "")


def test_database_error_inside_handler_is_transient(db: Session) -> None:
    jid = queue.enqueue(db, "t_dbfail")
    db.commit()
    runner.run_one("w")
    job = _job(db, jid)
    assert job.status == JobStatus.queued and job.error_category == "transient"


def test_unknown_kind_is_dead(db: Session) -> None:
    jid = queue.enqueue(db, "no_such_kind")
    db.commit()
    runner.run_one("w")
    assert _job(db, jid).status == JobStatus.dead


def test_backoff_has_jitter_and_cap() -> None:
    values = {round(queue.backoff_seconds(3), 3) for _ in range(20)}
    assert len(values) > 1
    assert all(10.0 <= v <= 20.0 for v in values)  # base 5 * 2^2 = 20, equal jitter -> [10, 20]
    assert queue.backoff_seconds(30) <= 900


def test_worker_crash_recovery_via_lease_expiry(db: Session) -> None:
    jid = queue.enqueue(db, "t_ok", {"v": "recovered"})
    db.commit()
    claimed = queue.claim(db, "dead-worker", lease_seconds=60)  # worker "dies" holding the job
    assert claimed is not None
    assert not runner.run_one("w2")  # lease still valid -> not stolen
    db.execute(update(Job).values(locked_until=utcnow() - timedelta(seconds=1)))
    db.commit()
    assert runner.run_one("w2")
    job = _job(db, jid)
    assert job.status == JobStatus.succeeded and job.attempts == 2 and CALLS == ["recovered"]


def test_stale_worker_cannot_complete_reclaimed_job(db: Session) -> None:
    jid = queue.enqueue(db, "t_ok")
    db.commit()
    queue.claim(db, "old", lease_seconds=60)
    db.execute(update(Job).values(locked_until=utcnow() - timedelta(seconds=1)))
    db.commit()
    queue.claim(db, "new", lease_seconds=60)
    assert jid is not None
    assert queue.mark_succeeded(db, jid, "old") is False
    assert queue.mark_failed(db, jid, "old", error="x", category="transient") == "lost_lease"


def test_attempt_budget_enforced_on_repeated_lease_expiry(db: Session) -> None:
    jid = queue.enqueue(db, "t_ok", max_attempts=2)
    db.commit()
    for _ in range(2):
        queue.claim(db, "crashy", lease_seconds=60)
        db.execute(update(Job).values(locked_until=utcnow() - timedelta(seconds=1)))
        db.commit()
    runner.run_one("w")
    assert _job(db, jid).status == JobStatus.dead and CALLS == []


def test_concurrent_workers_never_double_process(db: Session) -> None:
    for i in range(40):
        queue.enqueue(db, "t_ok", {"v": str(i)})
    db.commit()

    def work(n: int) -> None:
        while runner.run_one(f"w{n}"):
            pass

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(CALLS, key=int) == [str(i) for i in range(40)]
    assert db.query(Job).filter(Job.status == JobStatus.succeeded).count() == 40


def test_failure_recording_survives_db_outage(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """If the DB is unavailable when recording a failure, the lease expiry recovers the job."""
    jid = queue.enqueue(db, "t_transient")
    db.commit()

    def boom(*a: object, **k: object) -> str:
        raise OperationalError("UPDATE", {}, Exception("db down"))

    monkeypatch.setattr(queue, "mark_failed", boom)
    runner.run_one("w")  # must not raise
    job = _job(db, jid)
    assert job.status == JobStatus.running and job.locked_by == "w"  # recoverable after lease


def test_queue_stats(db: Session) -> None:
    queue.enqueue(db, "t_ok")
    db.commit()
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    stats = queue.queue_stats(db)
    assert stats["queued"] == 1
