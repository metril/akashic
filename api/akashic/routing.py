"""Router whose routes document the error responses they can actually produce.

Every route built through :class:`ApiRouter` gets ``responses`` derived from:

* its dependencies: an authenticating dependency implies 401, a
  ``require_scope`` implies 403, and source scoping implies 404 (anything
  outside a limited token's sources answers 404);
* its parameters: any query/path/header/body input implies 422;
* its handler: every status code raised literally via ``HTTPException`` in the
  handler body (``status_code=409``, ``status.HTTP_410_GONE``, ...).

The call graph is followed without a depth limit (a visited set stops cycles),
through every ``akashic`` function reachable from the handler.

Calls are resolved statically: plain names, ``module.f()``, ``self.m()`` (with subclass
overrides), annotated parameters and ``x = Cls()`` locals, lazy ``import`` statements, and
constructors. A call on a receiver of unknown type is matched by method name against every
akashic class (``refresh_all`` imports the whole package first, so that is complete). Known
limits: ``super()`` calls, ``getattr``/dynamic dispatch, callbacks passed as arguments and
calls on non-akashic objects are not followed. The runtime response recorder in the test
suite is the backstop for what the static scan cannot see.

A raise whose status is not a literal (``HTTPException(status_code=err.status_code)``)
cannot be derived. It must be acknowledged *at the function that raises it* with
``@raises(401, 410)``, which lists every status it can produce; those codes are
added to every route that reaches the function. An unacknowledged dynamic raise
fails the route build, naming the route and the call chain.
"""
from __future__ import annotations

import ast
import inspect
import re
import textwrap
import weakref
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


def raises(*codes: int) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Acknowledge a non-literal ``HTTPException`` status in the decorated function.

    Lists every status the function can raise; routes reaching it document them.
    Place it directly above the ``def`` (below any route decorator).
    """
    if not codes or any(c < 400 for c in codes):
        raise ValueError("raises() needs one or more status codes >= 400")

    def mark(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn.__akashic_raises__ = frozenset(codes)  # type: ignore[attr-defined]
        return fn

    return mark


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
                    if kw.arg != "status_code":
                        continue
                    v = kw.value
                    if isinstance(v, ast.Constant) and isinstance(v.value, int):
                        found.add(v.value)
                    elif isinstance(v, ast.Attribute) and re.match(r"HTTP_\d{3}_", v.attr):
                        found.add(int(v.attr[5:8]))
                    else:  # a variable status: cannot be derived, needs @raises
                        dynamic = True
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


def _chain(expr: ast.expr) -> list[str] | None:
    """``a.b.c`` -> ``["a", "b", "c"]``; None for anything else."""
    parts: list[str] = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if not isinstance(expr, ast.Name):
        return None
    parts.append(expr.id)
    return parts[::-1]


_EXTERNAL = object()  # marks a local whose class is known and is not ours (SQLAlchemy session, httpx client, ...)


def _akashic(obj: Any) -> bool:
    if inspect.ismodule(obj):  # modules carry no ``__module__``; ``m.f()`` must follow into akashic modules
        return obj.__name__.startswith(SCANNED_MODULE_PREFIXES)
    mod = getattr(obj, "__module__", None) or getattr(type(obj), "__module__", "")
    return str(mod).startswith(SCANNED_MODULE_PREFIXES)


def _as_class(ann: Any, globs: dict[str, Any], external: bool = False) -> Any:
    """The akashic class named by an annotation (``X``, ``X | None``, ``Annotated[X, ...]``, or a string).
    With ``external=True`` a resolvable non-akashic class yields ``_EXTERNAL`` instead of None."""
    import typing

    if isinstance(ann, str):
        try:
            ann = eval(ann, globs)  # noqa: S307 - our own annotations
        except Exception:
            return None
    if inspect.isclass(ann):
        return ann if _akashic(ann) else (_EXTERNAL if external and ann is not type(None) else None)
    ext = None
    for arg in typing.get_args(ann):
        found = _as_class(arg, globs, external)
        if found is _EXTERNAL:
            ext = found
        elif found is not None:
            return found
    return ext


def _owner_class(fn: Any) -> Any:
    """The class a method was defined in, found from its qualified name."""
    parts = fn.__qualname__.split(".")[:-1]
    obj: Any = fn.__globals__.get(parts[0]) if parts else None
    for part in parts[1:]:
        obj = getattr(obj, part, None)
    return obj if inspect.isclass(obj) else None


def _local_types(fn: Any, tree: ast.AST) -> dict[str, Any]:
    """Variables whose class is known: annotated parameters, ``x = Cls(...)``, ``self``/``cls``."""
    out: dict[str, Any] = {}
    node = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
    if node is None:
        return out
    args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
    for a in args:
        if a.annotation is not None:
            cls = _as_class(ast.unparse(a.annotation), fn.__globals__, external=True)
            if cls is not None:
                out[a.arg] = cls
    owner = _owner_class(fn)
    if owner is not None and node.args.args:
        out[node.args.args[0].arg] = owner
    for n in ast.walk(node):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name):
            cls = fn.__globals__.get(n.value.func.id)
            if inspect.isclass(cls):
                out.update({t.id: (cls if _akashic(cls) else _EXTERNAL) for t in n.targets if isinstance(t, ast.Name)})
    return out


def _subclass_methods(cls: Any, name: str) -> list[Any]:
    out = []
    for sub in cls.__subclasses__():
        meth = sub.__dict__.get(name)
        if meth is not None:
            out.append(meth)
        out.extend(_subclass_methods(sub, name))
    return out


def _local_imports(tree: ast.AST) -> dict[str, Any]:
    """Names bound by ``import`` statements inside the function (handlers import lazily a lot)."""
    import importlib

    out: dict[str, Any] = {}
    for n in ast.walk(tree):
        try:
            if isinstance(n, ast.Import):
                for a in n.names:
                    mod = importlib.import_module(a.name)
                    out[a.asname or a.name.split(".")[0]] = mod if a.asname else importlib.import_module(a.name.split(".")[0])
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                mod = importlib.import_module(n.module)
                for a in n.names:
                    out[a.asname or a.name] = getattr(mod, a.name, None) or importlib.import_module(f"{n.module}.{a.name}")
        except ImportError:
            continue
    return out


def _callee(
    node: ast.Call, fn: Any, local_types: dict[str, Any], scope: dict[str, Any] | None = None,
) -> tuple[list[Any], str | None]:
    """Resolve a call to the akashic functions it can run.

    Followed: ``f()``, ``module.f()``, ``GLOBAL_OBJ.method()``, ``Cls()`` (its
    ``__init__``), ``self.method()`` / ``cls.method()`` (including subclass
    overrides), and ``var.method()`` where ``var`` is an annotated parameter or
    ``var = Cls(...)``, and ``module.f()`` on an akashic module (``import akashic.x as m``). Returns ``(targets, unresolved_method_name)``: the second
    is set when the receiver's type is unknown (a variable bound to a call's
    result, an attribute assigned in ``__init__``, ...); the scanner then
    considers every akashic method of that name. Calls on non-akashic receivers
    (SQLAlchemy sessions, stdlib, ...) are ignored.
    """
    globs = scope if scope is not None else fn.__globals__
    chain = _chain(node.func)
    if chain is None:
        # ``Cls(...).method()``, ``f().method()``: receiver is a call result.
        if isinstance(node.func, ast.Attribute):
            return [], node.func.attr
        return [], None
    root = chain[0]
    if root in local_types:
        obj: Any = local_types[root]
        if obj is _EXTERNAL:
            return [], None  # receiver's class is known and is not ours: nothing to follow
    elif root in globs:
        obj = globs[root]
    else:
        return [], (chain[-1] if len(chain) > 1 else None)
    for attr in chain[1:]:
        if not _akashic(obj):
            return [], None  # external receiver: not ours to follow
        obj = getattr(obj, attr, None)
        if obj is None:  # instance attribute set at runtime: type unknown
            return [], chain[-1]
    if len(chain) == 2 and root in local_types:  # overrides in subclasses may run instead
        return [obj, *_subclass_methods(local_types[root], chain[1])], None
    if inspect.isclass(obj) and _akashic(obj):
        init = obj.__dict__.get("__init__")
        return ([init] if init is not None else []), None
    return [obj], None


_DIRECT_CACHE: dict[Any, tuple[set[int], bool, list[Any], list[str]] | None] = {}


def _direct(fn: Any) -> tuple[set[int], bool, list[Any], list[str]] | None:
    """(codes, unacknowledged dynamic raise, callees, unresolved method names) for ``fn`` alone,
    or None if not scannable."""
    if fn in _DIRECT_CACHE:
        return _DIRECT_CACHE[fn]
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, SyntaxError):
        _DIRECT_CACHE[fn] = None
        return None
    codes, dynamic = _scan_raises(tree, fn.__globals__)
    declared = getattr(fn, "__akashic_raises__", None)
    if declared:
        codes |= declared
        dynamic = False
    callees: list[Any] = []
    unresolved: list[str] = []
    local_types = _local_types(fn, tree)
    scope = {**fn.__globals__, **_local_imports(tree)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            targets, name = _callee(node, fn, local_types, scope)
            for target in targets:
                target = inspect.unwrap(getattr(target, "__func__", target))
                if isinstance(target, (staticmethod, classmethod)):
                    target = target.__func__
                if inspect.isfunction(target):
                    callees.append(target)
            if name is not None and not name.startswith("__"):
                unresolved.append(name)
    result = (codes, dynamic, callees, unresolved)
    _DIRECT_CACHE[fn] = result
    return result


def _methods_named(name: str) -> list[Any]:
    """Every method called ``name`` on an akashic class currently loaded."""
    import sys

    out: list[Any] = []
    for mod_name, mod in list(sys.modules.items()):
        if not mod_name.startswith(SCANNED_MODULE_PREFIXES) or mod is None:
            continue
        for cls in list(vars(mod).values()):
            if inspect.isclass(cls) and cls.__module__ == mod_name:
                meth = cls.__dict__.get(name)
                if isinstance(meth, (staticmethod, classmethod)):
                    meth = meth.__func__
                if inspect.isfunction(meth):
                    out.append(meth)
    return out


def scan_raises(
    endpoint: Callable[..., Any], _seen: set[Any] | None = None,
) -> tuple[set[int], list[list[str]]]:
    """Status codes raised by ``endpoint`` and, transitively, by every ``akashic``
    function it can reach; also the call chains ending in an unacknowledged
    non-literal raise. ``_seen`` (shared across a route's entry points) is the
    visited set, so cycles terminate and no depth limit is needed."""
    seen = _seen if _seen is not None else set()
    codes: set[int] = set()
    bad: list[list[str]] = []

    def visit(target: Any, chain: list[str]) -> None:
        fn = inspect.unwrap(getattr(target, "__func__", target))
        if fn in seen or not inspect.isfunction(fn):
            return
        seen.add(fn)
        if not getattr(fn, "__module__", "").startswith(SCANNED_MODULE_PREFIXES):
            return
        res = _direct(fn)
        if res is None:
            return
        c, dynamic, callees, unresolved = res
        codes.update(c)
        here = chain + [fn.__qualname__]
        if dynamic:
            bad.append(here)
        for callee in callees:
            visit(callee, here)
        for name in unresolved:  # receiver type unknown: every akashic method of that name
            for callee in _methods_named(name):
                visit(callee, here)

    visit(endpoint, [])
    return codes, bad


class DocumentedRoute(APIRoute):
    def __init__(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> None:
        super().__init__(path, endpoint, **kwargs)
        self.refresh()

    def refresh(self) -> None:
        """(Re)derive the documented error responses from the current call graph."""
        seen: set[Any] = set()
        codes, bad = scan_raises(self.endpoint, seen)
        dependants = list(_walk_dependants(self.dependant))
        for dep in dependants:
            c, b = scan_raises(dep.call, seen)
            codes |= c
            bad += b
        if bad:
            chains = "; ".join(" -> ".join(chain) for chain in bad)
            raise RuntimeError(
                f"{self.path}: raises with a non-literal status code that is not acknowledged "
                f"(call chain: {chains}); decorate the raising function with @raises(<codes>)"
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


_ROUTERS: "list[weakref.ref[ApiRouter]]" = []


class ApiRouter(APIRouter):
    """APIRouter whose routes derive their documented error responses."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("route_class", DocumentedRoute)
        super().__init__(*args, **kwargs)
        _ROUTERS.append(weakref.ref(self))


_ALL_LOADED = False


def import_all_modules(*, strict: bool = False) -> None:
    """Import every ``akashic`` submodule so the method-name fallback sees all classes."""
    global _ALL_LOADED
    import importlib
    import logging
    import pkgutil
    import akashic

    def _on_error(name: str) -> None:
        if strict:
            raise ImportError(name)

    for mod in pkgutil.walk_packages(akashic.__path__, "akashic.", onerror=_on_error):
        try:
            importlib.import_module(mod.name)
        except Exception as exc:  # noqa: BLE001
            if strict:
                raise
            logging.getLogger(__name__).warning("route scan: could not import %s: %s", mod.name, exc)
    _ALL_LOADED = True


_REFRESHED = False


def refresh_all(*, force: bool = False) -> None:
    """Import every module, then re-derive every route's responses against the complete call graph.

    Routes are first derived while their router module is being imported, when only part of the
    package is loaded; this pass closes that gap. Call it before routers are included in an app.
    """
    global _REFRESHED
    if _REFRESHED and not force:  # routes are module-level singletons: once per process is enough
        return
    _REFRESHED = True
    import_all_modules()
    _DIRECT_CACHE.clear()
    for ref in list(_ROUTERS):
        router = ref()
        for route in (router.routes if router is not None else ()):
            if isinstance(route, DocumentedRoute):
                route.refresh()
