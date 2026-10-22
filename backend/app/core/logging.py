"""Structured JSON logging with request IDs and secret redaction."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.security.redaction import redact, redact_text

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
job_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("job_id", default=None)

_RESERVED = set(logging.LogRecord("x", 0, "x", 0, "x", None, None).__dict__.keys()) | {
    "message",
    "asctime",
}


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_text(record.getMessage()),
        }
        if rid := request_id_var.get():
            payload["request_id"] = rid
        if jid := job_id_var.get():
            payload["job_id"] = jid
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        if extras:
            payload.update(redact(extras))
        if record.exc_info:
            payload["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


class RedactingTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            k: v for k, v in record.__dict__.items() if k not in _RESERVED and k != "color_message"
        }
        if extras:
            base += " " + json.dumps(redact(extras), default=str)
        rid = request_id_var.get()
        return redact_text(f"[{rid}] {base}" if rid else base)


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        RedactingJsonFormatter()
        if json_logs
        else RedactingTextFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # httpx logs full URLs at INFO (could include query-string keys); keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
