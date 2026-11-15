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
from starlette.exceptions import HTTPException as StarletteHTTPException

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


# Methods whose receiver is a local variable (so the call cannot be resolved
# statically) but which are known to raise a fixed status.
# Only functions defined in these modules are followed.
SCANNED_MODULE_PREFIXES: tuple[str, ...] = ("akashic",)

_METHODS_RAISING: dict[str, set[int]] = {"ensure": {403}, "ensure_unrestricted": {403}}


def _is_http_exception(fn: ast.expr, globs: dict[str, Any]) -> bool:
    """True for a call target that is (a subclass of) Starlette's HTTPException."""
    name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
    if name.endswith("HTTPException"):
        return True
    target = globs.get(name) if isinstance(fn, ast.Name) else None
    return inspect.isclass(target) and issubclass(target, StarletteHTTPException)


def _scan_raises(tree: ast.AST, globs: dict[str, Any]) -> tuple[set[int], bool]:
    """(literal status codes >= 400, whether any raise uses a non-literal status)."""
    found: set[int] = set()
    dynamic = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fname = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if fname in _METHODS_RAISING:  # e.g. principal.ensure(...) on a local variable
                found |= _METHODS_RAISING[fname]
            if fname.endswith("Response"):  # returned (not raised) error responses
                for kw in node.keywords:
                    if kw.arg == "status_code" and isinstance(kw.value, (ast.Constant, ast.Attribute)):
                        v = kw.value
                        code = v.value if isinstance(v, ast.Constant) else (
                            int(v.attr[5:8]) if re.match(r"HTTP_\d{3}_", v.attr) else 0
                        )
                        if isinstance(code, int) and code >= 400:
                            found.add(code)
        if not (isinstance(node, ast.Call) and _is_http_exception(node.func, globs)):
            continue
        cands = [kw.value for kw in node.keywords if kw.arg == "status_code"] + node.args[:1]
        if not cands:
            continue
        for c in cands:
            if isinstance(c, ast.Constant) and isinstance(c.value, int):
                found.add(c.value)
            elif isinstance(c, ast.Attribute) and re.match(r"HTTP_\d{3}_", c.attr):
                found.add(int(c.attr[5:8]))
            else:
                dynamic = True
    return {c for c in found if c >= 400}, dynamic


def _callee(node: ast.Call, globs: dict[str, Any]) -> Any:
    """Resolve ``f(...)``, ``module.f(...)`` and ``GLOBAL_OBJ.method(...)`` to a function."""
    fn = node.func
    if isinstance(fn, ast.Name):
        return globs.get(fn.id)
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
        base = globs.get(fn.value.id)
        return getattr(base, fn.attr, None) if base is not None else None
    return None


def scan_raises(
    endpoint: Callable[..., Any], _seen: set[Any] | None = None, _depth: int = 4,
) -> tuple[set[int], bool]:
    """Status codes raised by ``endpoint`` and, transitively, by the ``akashic``
    functions and methods it calls; the flag reports a raise whose status is not literal."""
    seen = _seen if _seen is not None else set()
    fn = inspect.unwrap(getattr(endpoint, "__func__", endpoint))
    if fn in seen or not inspect.isfunction(fn):
        return set(), False
    seen.add(fn)
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, SyntaxError):
        return set(), False
    codes, dynamic = _scan_raises(tree, fn.__globals__)
    if _depth > 0:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = _callee(node, fn.__globals__)
                target = getattr(target, "__func__", target)
                if inspect.isfunction(target) and getattr(target, "__module__", "").startswith(SCANNED_MODULE_PREFIXES):
                    c, d = scan_raises(target, seen, _depth - 1)
                    codes |= c
                    dynamic = dynamic or d
    return codes, dynamic


class DocumentedRoute(APIRoute):
    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        super().__init__(path, endpoint, **kwargs)
        seen: set[Any] = set()
        codes, dynamic = scan_raises(endpoint, seen)
        dependants = list(_walk_dependants(self.dependant))
        for dep in dependants:
            c, d = scan_raises(dep.call, seen)
            codes |= c
            dynamic = dynamic or d
        if dynamic and not any(int(k) >= 400 for k in (kwargs.get("responses") or {}) if str(k).isdigit()):
            raise RuntimeError(
                f"{path}: a handler or helper raises with a non-literal status code; "
                "declare the possible codes with responses=errors(...)"
            )
        names = {getattr(d.call, "__name__", "") for d in dependants}
        scoped = [d.call for d in dependants if getattr(d.call, "required_scope", None) is not None]
        if names & AUTH_DEPENDENCIES:
            codes.add(401)
        if scoped:
            codes.add(403)
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
