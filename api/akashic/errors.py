"""Uniform error envelope: exception handlers + request-id middleware."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from akashic.schemas.common import ErrorResponse

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    426: "upgrade_required",
    429: "rate_limited",
    500: "internal_error",
    502: "bad_gateway",
    503: "service_unavailable",
}


def error_code(status_code: int) -> str:
    return _CODES.get(status_code, f"http_{status_code}")


def new_request_id() -> str:
    return uuid.uuid4().hex


def request_id_of(scope_or_request: Any) -> str:
    scope = getattr(scope_or_request, "scope", scope_or_request)
    return scope.get("state", {}).get("request_id") or new_request_id()


def error_body(status_code: int, message: str, request_id: str, detail: Any = None) -> dict[str, Any]:
    return ErrorResponse(
        code=error_code(status_code), message=message, detail=detail, request_id=request_id,
    ).model_dump(mode="json")


def error_response(
    status_code: int, message: str, request_id: str, detail: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        error_body(status_code, message, request_id, detail),
        status_code=status_code,
        headers={**(headers or {}), REQUEST_ID_HEADER: request_id},
    )


def _split_detail(status_code: int, detail: Any) -> tuple[str, Any]:
    """Map an HTTPException ``detail`` onto (message, structured extra)."""
    if isinstance(detail, str):
        return detail, None
    if isinstance(detail, dict):
        return str(detail.get("message") or error_code(status_code)), detail
    if isinstance(detail, list):
        return "Request failed validation", jsonable_encoder(detail)
    return str(detail), None


class RequestIdMiddleware:
    """Pure-ASGI: assign each request an id and echo it as ``X-Request-ID``."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        rid = new_request_id()
        scope.setdefault("state", {})["request_id"] = rid

        async def _send(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                headers.append((REQUEST_ID_HEADER.lower().encode(), rid.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, _send)


def install_error_handlers(app: FastAPI) -> None:
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message, detail = _split_detail(exc.status_code, exc.detail)
        return error_response(
            exc.status_code, message, request_id_of(request), detail,
            headers=dict(exc.headers) if exc.headers else None,
        )

    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(
            422, "Request failed validation", request_id_of(request),
            jsonable_encoder(exc.errors()),
        )

    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        rid = request_id_of(request)
        logger.exception("unhandled error (request_id=%s)", rid, exc_info=exc)
        return error_response(500, "Internal server error", rid)

    app.add_exception_handler(StarletteHTTPException, _http)
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(Exception, _unhandled)
