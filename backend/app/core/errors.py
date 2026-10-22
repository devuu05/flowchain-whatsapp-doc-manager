"""Application exceptions and their HTTP mapping."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

log = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 400
    code = "bad_request"
    message = "Bad request"

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        if code:
            self.code = code


class NotAuthenticated(AppError):
    status_code = 401
    code = "not_authenticated"
    message = "Authentication required"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"
    message = "You do not have access to this resource"


class NotFound(AppError):
    status_code = 404
    code = "not_found"
    message = "Resource not found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    message = "Conflict"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"
    message = "Payload too large"


class TooManyRequests(AppError):
    status_code = 429
    code = "rate_limited"
    message = "Too many requests"


class ServiceUnavailable(AppError):
    status_code = 503
    code = "unavailable"
    message = "Service unavailable"


def _body(code: str, message: str, details: object | None = None) -> dict[str, object]:
    err: dict[str, object] = {"code": code, "message": message, "request_id": request_id_var.get()}
    if details is not None:
        err["details"] = details
    return {"error": err}


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_body(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo submitted values back (they may contain secrets); only locations/messages.
        details = [
            {"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg", "")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422, content=_body("validation_error", "Invalid request", details)
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(code, str(exc.detail) if exc.detail else code),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", extra={"error_type": type(exc).__name__})
        return JSONResponse(status_code=500, content=_body("internal_error", "Internal error"))
