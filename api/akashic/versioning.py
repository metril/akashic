"""API versioning: the ``/api/v1`` and ``/ws/v1`` prefixes, and the rejection of unversioned paths.

Versioning lives here and in ``main.py`` only; routers declare resource paths.
Unversioned ``/api/*`` answers 410 with ``code=api_version_required``; unversioned
``/ws/*`` is refused. Two exceptions: the Swagger UI under ``/api/docs``, and the
browser-facing OIDC / source-OAuth callbacks, which 308 to their v1 path so that
redirect URIs already registered at an identity provider keep working for now.
"""
from __future__ import annotations

from akashic.errors import error_response, request_id_of

API_PREFIX = "/api/v1"
WS_PREFIX = "/ws/v1"

# Unversioned path -> versioned path, for callbacks registered with external providers.
_CALLBACK_REDIRECTS = {
    "/api/auth/oidc/callback": f"{API_PREFIX}/auth/oidc/callback",
    "/api/oauth/callback": f"{API_PREFIX}/oauth/callback",
}

_WS_CLOSE_POLICY_VIOLATION = 1008


def _is_unversioned_api(path: str) -> bool:
    if path.startswith((f"{API_PREFIX}/", "/api/docs/")) or path in (API_PREFIX, "/api/docs"):
        return False
    return path == "/api" or path.startswith("/api/")


def _is_unversioned_ws(path: str) -> bool:
    if path.startswith(f"{WS_PREFIX}/") or path == WS_PREFIX:
        return False
    return path == "/ws" or path.startswith("/ws/")


class UnversionedPathMiddleware:
    """Pure-ASGI: reject or redirect requests that omit the API version."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        kind, path = scope["type"], scope.get("path", "")
        if kind == "http" and _is_unversioned_api(path):
            target = _CALLBACK_REDIRECTS.get(path)
            if target is not None and scope["method"] in ("GET", "HEAD"):
                qs = scope.get("query_string", b"").decode("latin-1")
                location = f"{target}?{qs}" if qs else target
                response = error_response(
                    308, f"Moved permanently: use {location}", request_id_of(scope),
                    headers={"Location": location}, code="api_version_required",
                )
                return await response(scope, receive, send)
            response = error_response(
                410,
                f"Unversioned API paths were removed; use {API_PREFIX}{path[len('/api'):]}",
                request_id_of(scope), code="api_version_required",
            )
            return await response(scope, receive, send)
        if kind == "websocket" and _is_unversioned_ws(path):
            await receive()  # consume websocket.connect
            return await send({
                "type": "websocket.close", "code": _WS_CLOSE_POLICY_VIOLATION,
                "reason": f"api_version_required: use {WS_PREFIX}{path[len('/ws'):]}",
            })
        await self.app(scope, receive, send)
