"""Job error classification."""

from __future__ import annotations


class JobError(Exception):
    category = "transient"


class TransientJobError(JobError):
    """Retry later with backoff (timeouts, 429, 5xx, connection errors)."""

    category = "transient"

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PermanentJobError(JobError):
    """Do not retry (validation failures, 4xx auth errors, unsupported input)."""

    category = "permanent"
