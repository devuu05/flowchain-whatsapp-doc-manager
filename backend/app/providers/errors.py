"""Provider error type shared by all external adapters."""

from __future__ import annotations


class ProviderError(Exception):
    """An external provider call failed.

    `transient` errors (timeouts, 408/429/5xx, connection errors) may be retried; others
    (auth failures, validation errors, unsupported input) must not be.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        transient: bool,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.transient = transient
        self.status_code = status_code
        self.retry_after = retry_after

    def __str__(self) -> str:
        code = f" [{self.status_code}]" if self.status_code else ""
        return f"{self.provider}{code}: {self.args[0]}"


def is_transient_status(status: int) -> bool:
    return status in (408, 425, 429) or status >= 500
