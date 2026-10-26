"""One error shape everywhere (backend-design.md section 10)."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.deps import AuthFailure
from app.core.execute import ChatError
from app.utils import new_request_id

log = logging.getLogger("gateway")

_DEFAULT_CODES = {
    400: "invalid_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "invalid_request",
    429: "rate_limit_exceeded",
    500: "internal_error",
    502: "upstream_error",
    503: "no_provider_available",
}


def error_response(
    status: int,
    code: str,
    message: str,
    request_id: str = "",
    headers: dict[str, str] | None = None,
    attempts: list[dict] | None = None,
) -> JSONResponse:
    error: dict = {"code": code, "message": message, "request_id": request_id}
    if attempts:
        error["attempts"] = attempts
    return JSONResponse(
        status_code=status,
        content={"error": error},
        headers={"X-Request-Id": request_id, **(headers or {})},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AuthFailure)
    async def _auth_failure(request: Request, exc: AuthFailure):
        request_id = getattr(request.state, "request_id", "") or new_request_id()
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return error_response(exc.status, exc.code, exc.message, request_id, headers)

    @app.exception_handler(ChatError)
    async def _chat_error(request: Request, exc: ChatError):
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
        return error_response(
            exc.status, exc.code, exc.message, exc.request_id, headers, exc.attempts or None
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        request_id = getattr(request.state, "request_id", "") or new_request_id()
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return error_response(
            exc.status_code,
            _DEFAULT_CODES.get(exc.status_code, "error"),
            message,
            request_id,
            dict(getattr(exc, "headers", None) or {}),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", "") or new_request_id()
        first = (exc.errors() or [{}])[0]
        location = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query"))
        message = first.get("msg", "Invalid request.")
        if location:
            message = f"{location}: {message}"
        return error_response(400, "invalid_request", message, request_id)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", "") or new_request_id()
        log.exception("Unhandled error [%s]", request_id)
        return error_response(500, "internal_error", "Unexpected server error.", request_id)
