"""Job handler registry. Handlers run inside a DB transaction committed by the runner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Job

Handler = Callable[[Session, Job], None]
DeadHandler = Callable[[Session, Job], None]


@dataclass
class JobSpec:
    handler: Handler
    on_dead: DeadHandler | None = None


HANDLERS: dict[str, JobSpec] = {}


def job_handler(kind: str, on_dead: DeadHandler | None = None) -> Callable[[Handler], Handler]:
    def decorator(fn: Handler) -> Handler:
        HANDLERS[kind] = JobSpec(handler=fn, on_dead=on_dead)
        return fn

    return decorator
