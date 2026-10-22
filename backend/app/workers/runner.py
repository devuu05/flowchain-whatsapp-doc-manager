"""Worker process: claims jobs from Postgres and executes registered handlers.

Run standalone with `python -m app.workers.runner`, or embedded in the API process with
EMBEDDED_WORKER=true (see ADR 0001 and docs/operations/hosting.md).
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import uuid

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from app.core.config import get_settings
from app.core.logging import configure_logging, job_id_var
from app.db.session import get_sessionmaker
from app.models import Job
from app.workers import queue
from app.workers.errors import PermanentJobError, TransientJobError
from app.workers.registry import HANDLERS

log = logging.getLogger("app.worker")
MAINTENANCE_INTERVAL_SECONDS = 300
_heartbeat: dict[str, float] = {}


def last_heartbeat() -> dict[str, float]:
    return dict(_heartbeat)


def _load_handlers() -> None:
    import app.workers.handlers  # noqa: F401  (registers handlers)


def run_one(worker_id: str) -> bool:
    """Claim and execute at most one job. Returns True if a job was processed."""
    Session = get_sessionmaker()
    with Session() as db:
        job = queue.claim(db, worker_id)
    if job is None:
        return False

    token = job_id_var.set(str(job.id))
    try:
        _execute(job, worker_id)
    finally:
        job_id_var.reset(token)
    return True


def _execute(job: Job, worker_id: str) -> None:
    Session = get_sessionmaker()
    spec = HANDLERS.get(job.kind)
    started = time.perf_counter()
    try:
        if spec is None:
            raise PermanentJobError(f"No handler for job kind {job.kind!r}")
        if job.attempts > job.max_attempts:
            raise PermanentJobError("Attempt budget exhausted (lease expired repeatedly)")
        with Session() as db:
            fresh = db.get(Job, job.id)
            assert fresh is not None
            spec.handler(db, fresh)
            if queue.mark_succeeded(db, job.id, worker_id):
                db.commit()
            else:
                db.rollback()
                log.warning("job_lease_lost", extra={"kind": job.kind})
                return
        log.info(
            "job_succeeded",
            extra={
                "kind": job.kind,
                "attempt": job.attempts,
                "ms": round((time.perf_counter() - started) * 1000),
            },
        )
    except Exception as exc:
        category = "permanent" if isinstance(exc, PermanentJobError) else "transient"
        retry_after = exc.retry_after if isinstance(exc, TransientJobError) else None
        if isinstance(exc, OperationalError | DBAPIError):
            category = "transient"
        message = f"{type(exc).__name__}: {exc}"
        log.warning(
            "job_failed",
            extra={"kind": job.kind, "attempt": job.attempts, "category": category},
            exc_info=not isinstance(exc, TransientJobError | PermanentJobError),
        )
        try:
            with Session() as db:
                status = queue.mark_failed(
                    db,
                    job.id,
                    worker_id,
                    error=message,
                    category=category,
                    retry_after=retry_after,
                )
                if status == "dead" and spec is not None and spec.on_dead is not None:
                    dead_job = db.get(Job, job.id)
                    if dead_job is not None:
                        spec.on_dead(db, dead_job)
                        db.commit()
                if status == "dead":
                    log.error("job_dead", extra={"kind": job.kind, "attempts": job.attempts})
        except Exception:
            # DB unavailable: the lease will expire and the job will be reclaimed.
            log.exception("job_failure_not_recorded", extra={"kind": job.kind})


def run_maintenance() -> None:
    from app.services.maintenance import sweep

    Session = get_sessionmaker()
    with Session() as db:
        got = db.execute(text("SELECT pg_try_advisory_xact_lock(424242)")).scalar()
        if not got:
            return
        sweep(db)
        db.commit()


class Worker:
    def __init__(self, concurrency: int | None = None) -> None:
        self.concurrency = concurrency or get_settings().worker_concurrency
        self.stop_event = threading.Event()
        self.threads: list[threading.Thread] = []
        self.base_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"

    def _loop(self, idx: int) -> None:
        worker_id = f"{self.base_id}:{idx}"
        poll = get_settings().worker_poll_seconds
        last_maintenance = 0.0
        while not self.stop_event.is_set():
            _heartbeat[worker_id] = time.time()
            try:
                if idx == 0 and time.time() - last_maintenance > MAINTENANCE_INTERVAL_SECONDS:
                    last_maintenance = time.time()
                    run_maintenance()
                if run_one(worker_id):
                    continue
            except Exception:
                log.exception("worker_loop_error")
                self.stop_event.wait(min(30.0, poll * 5))
                continue
            self.stop_event.wait(poll)

    def start(self) -> None:
        _load_handlers()
        for i in range(self.concurrency):
            t = threading.Thread(target=self._loop, args=(i,), name=f"worker-{i}", daemon=True)
            t.start()
            self.threads.append(t)
        log.info("worker_started", extra={"concurrency": self.concurrency})

    def stop(self, timeout: float = 30.0) -> None:
        self.stop_event.set()
        for t in self.threads:
            t.join(timeout=timeout)
        log.info("worker_stopped")


def main() -> None:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    worker = Worker()
    worker.start()

    def _shutdown(*_: object) -> None:
        worker.stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    while not worker.stop_event.is_set():
        time.sleep(0.5)
    worker.stop()


if __name__ == "__main__":
    main()
