"""Liveness and readiness probes."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.db.session import get_sessionmaker, ping_database
from app.workers import queue
from app.workers.runner import last_heartbeat

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness: the process is up. Does not touch dependencies."""
    return {"status": "ok"}


@router.get("/health/ready")
def ready() -> JSONResponse:
    """Readiness: database reachable; reports queue backlog and worker heartbeat."""
    checks: dict[str, Any] = {}
    ok = True
    try:
        ping_database()
        checks["database"] = "ok"
        with get_sessionmaker()() as db:
            stats = queue.queue_stats(db)
        checks["queue"] = {
            "queued": stats.get("queued", 0),
            "running": stats.get("running", 0),
            "dead": stats.get("dead", 0),
            "oldest_due_seconds": round(stats["oldest_due_seconds"]),
        }
    except Exception:
        ok = False
        checks["database"] = "unavailable"
    if get_settings().embedded_worker:
        beats = last_heartbeat()
        newest = max(beats.values()) if beats else 0
        alive = time.time() - newest < 120
        checks["worker"] = "ok" if alive else "stale"
        ok = ok and alive
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "ok" if ok else "degraded", "checks": checks},
    )
