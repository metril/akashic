"""Router whose routes document the error responses they can actually produce.

Every route built through :class:`ApiRouter` gets ``responses`` derived from:

* its dependencies: an authenticating dependency implies 401, a
  ``require_scope`` implies 403, and source scoping implies 404 (anything
  outside a limited token's sources answers 404);
* its parameters: any query/path/header/body input implies 422;
* its handler: every status code raised literally via ``HTTPException`` in the
  handler body (``status_code=409``, ``status.HTTP_410_GONE``, ...).

Codes a handler raises indirectly (through a helper) are declared with an
explicit ``responses=`` on the route.
"""
from __future__ import annotations

import ast
import inspect
import re
import textwrap
from http import HTTPStatus
from typing import Any, Callable

from fastapi import APIRouter
from fastapi.routing import APIRoute

from akashic.schemas.common import ErrorResponse

AUTH_DEPENDENCIES = frozenset({"get_principal", "get_ingest_user", "verify_scanner_jwt"})

_DESCRIPTIONS = {
    400: "Malformed or unacceptable request.",
    401: "Missing, invalid or expired credentials.",
    403: "Authenticated but not permitted.",
    404: "Resource not found.",
    409: "Conflicts with the current resource state.",
    410: "Resource is gone or expired.",
    413: "Request body too large.",
    422: "Request failed validation.",
    426: "Client must upgrade.",
    429: "Too many requests.",
}


def error_response(code: int) -> dict[str, Any]:
    return {"model": ErrorResponse, "description": _DESCRIPTIONS.get(code) or HTTPStatus(code).phrase}


def errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    """Explicit ``responses=`` helper: ``responses=errors(409, 502)``."""
    return {c: error_response(c) for c in codes}


def _walk_dependants(dependant):
    for sub in dependant.dependencies:
        yield sub
        yield from _walk_dependants(sub)


def _literal_codes(tree: ast.AST) -> set[int]:
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not ((isinstance(fn, ast.Name) and fn.id == "HTTPException")
                or (isinstance(fn, ast.Attribute) and fn.attr == "HTTPException")):
            continue
        cands = [kw.value for kw in node.keywords if kw.arg == "status_code"] + node.args[:1]
        for c in cands:
            if isinstance(c, ast.Constant) and isinstance(c.value, int):
                found.add(c.value)
            elif isinstance(c, ast.Attribute):
                m = re.match(r"HTTP_(\d{3})_", c.attr)
                if m:
                    found.add(int(m.group(1)))
    return {c for c in found if c >= 400}


def raised_status_codes(
    endpoint: Callable[..., Any], _seen: set[Any] | None = None, _depth: int = 3,
) -> set[int]:
    """Status codes raised via literal ``HTTPException(...)`` in ``endpoint`` and,
    transitively (a few levels), in the ``akashic`` helper functions it calls."""
    seen = _seen if _seen is not None else set()
    fn = inspect.unwrap(endpoint)
    if fn in seen or not inspect.isfunction(fn):
        return set()
    seen.add(fn)
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, SyntaxError):
        return set()
    codes = _literal_codes(tree)
    if _depth > 0:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                target = fn.__globals__.get(node.func.id)
                if (
                    inspect.isfunction(target)
                    and getattr(target, "__module__", "").startswith("akashic")
                ):
                    codes |= raised_status_codes(target, seen, _depth - 1)
    return codes


class DocumentedRoute(APIRoute):
    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        super().__init__(path, endpoint, **kwargs)
        seen: set[Any] = set()
        codes = set(raised_status_codes(endpoint, seen))
        dependants = list(_walk_dependants(self.dependant))
        for dep in dependants:
            codes |= raised_status_codes(dep.call, seen)
        names = {getattr(d.call, "__name__", "") for d in dependants}
        scoped = [d.call for d in dependants if getattr(d.call, "required_scope", None) is not None]
        if names & AUTH_DEPENDENCIES:
            codes.add(401)
        if scoped:
            codes.add(403)
            if any(getattr(c, "source_scoped", False) for c in scoped):
                codes.add(404)
        d = self.dependant
        if d.path_params or d.query_params or d.header_params or d.cookie_params or d.body_params:
            codes.add(422)
        for code in sorted(codes):
            self.responses.setdefault(code, error_response(code))


class ApiRouter(APIRouter):
    """APIRouter whose routes derive their documented error responses."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("route_class", DocumentedRoute)
        super().__init__(*args, **kwargs)
