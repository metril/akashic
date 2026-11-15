"""OpenAPI contract: every route is documented, and the committed spec is current.

Existing gaps live in ``tests/openapi_allowlist.py`` (a ratchet): a *new*
violation fails, and so does an allowlist entry that has since been fixed,
so the list can only shrink. Regenerate it after fixing things with
``make openapi-allowlist``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.datastructures import DefaultPlaceholder
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from akashic.main import create_app
from akashic.auth.scopes import ROLE_SCOPES, SCOPES
from akashic.openapi import SCANNER_TAG, TAGS
from akashic.schemas.common import ErrorResponse
from tests.openapi_allowlist import ALLOWLIST

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "openapi.json"

# Dependencies that authenticate the caller (directly or transitively).
AUTH_DEPENDENCIES = frozenset({
    "get_principal", "get_ingest_user", "verify_scanner_jwt",
})

# Authenticated, but deliberately not gated by a scope: they describe the caller
# itself, which every valid credential may do.
AUTHENTICATED_ONLY = frozenset({
    "GET /api/v1/users/me",
    "GET /api/v1/scopes",
})

# Tags whose routes return source-scoped data. They must declare
# ``require_scope(..., source_scoped=True)`` (and apply the limit; the
# behavioural check is tests/test_api_tokens.py); no other route may.
DATA_TAGS = frozenset({
    "search", "entries", "browse", "duplicates", "analytics", "dashboard", "storage",
    "access", "effective-permissions", "scans", "sources", "tags",
})

# Routes that are intentionally unauthenticated: they mint credentials,
# bootstrap a scanner, or are browser redirects.
PUBLIC_ROUTES = frozenset({
    "GET /api/v1/auth/providers",
    "GET /api/v1/auth/oidc/login",
    "GET /api/v1/auth/oidc/callback",
    "POST /api/v1/auth/ldap/login",
    "POST /api/v1/auth/refresh",
    "POST /api/v1/auth/logout",
    "POST /api/v1/users/register",
    "POST /api/v1/users/login",
    "POST /api/v1/scanners/claim",
    "POST /api/v1/scanners/discover",
    "GET /api/v1/scanners/discover/{discovery_id}",
    "GET /api/v1/oauth/callback",
})

# Built-in FastAPI schemas we don't author.
_SKIP_SCHEMAS = frozenset({"HTTPValidationError", "ValidationError"})

CATEGORIES = (
    "summary", "response_model", "error_responses", "tag", "operation_id", "auth", "scope", "description",
)


def _dependency_names(route: APIRoute) -> set[str]:
    names: set[str] = set()

    def walk(dependant) -> None:
        for sub in dependant.dependencies:
            names.add(getattr(sub.call, "__name__", ""))
            walk(sub)

    walk(route.dependant)
    return names


def _required_scopes(route: APIRoute) -> list[str]:
    """Scopes demanded by the route's ``require_scope`` dependencies."""
    found: list[str] = []

    def walk(dependant) -> None:
        for sub in dependant.dependencies:
            scope = getattr(sub.call, "required_scope", None)
            if scope is not None:
                found.append(scope)
            walk(sub)

    walk(route.dependant)
    return found


def _source_scoped(route: APIRoute) -> bool:
    """True when the route's ``require_scope`` declares it honours source limits."""
    found: list[bool] = []

    def walk(dependant) -> None:
        for sub in dependant.dependencies:
            if getattr(sub.call, "required_scope", None) is not None:
                found.append(bool(getattr(sub.call, "source_scoped", False)))
            walk(sub)

    walk(route.dependant)
    return any(found)


def _documented_errors(route: APIRoute) -> set[int]:
    return {
        int(code) for code, body in route.responses.items()
        if str(code).isdigit() and int(code) >= 400 and body.get("model") is ErrorResponse
    }


def _raised_literals(route: APIRoute) -> set[int]:
    """Status codes the handler body raises via a literal ``HTTPException(...)``."""
    import ast
    import inspect
    import re
    import textwrap

    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(inspect.unwrap(route.endpoint))))
    except (OSError, TypeError, SyntaxError):
        return set()
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and "HTTPException" in ast.unparse(node.func):
            args = [kw.value for kw in node.keywords if kw.arg == "status_code"] + node.args[:1]
            for a in args:
                text = ast.unparse(a)
                m = re.fullmatch(r"\d{3}", text) or re.search(r"HTTP_(\d{3})_", text)
                if m:
                    out.add(int(m.group(m.lastindex or 0)))
    return {c for c in out if c >= 400}


def _required_errors(route: APIRoute) -> set[int]:
    """Codes implied by dependencies and params, plus every code the handler raises literally."""
    req = _raised_literals(route)
    if _dependency_names(route) & AUTH_DEPENDENCIES:
        req.add(401)
    if _required_scopes(route):
        req.add(403)
    d = route.dependant
    if d.path_params or d.query_params or d.header_params or d.cookie_params or d.body_params:
        req.add(422)
    return req


def _is_user_route(key: str, route: APIRoute) -> bool:
    """True for routes a user credential calls (not public, scanner or ingest)."""
    return key not in PUBLIC_ROUTES and SCANNER_TAG not in (route.tags or [])


def _flatten(routes) -> list:
    """Resolve the app's route table into API routes, on old and new FastAPI.

    Newer FastAPI keeps ``include_router`` results as lazy ``_IncludedRouter``
    nodes whose effective routes (full path, merged tags/responses/dependencies)
    come from ``effective_candidates()``; older versions flatten eagerly.
    """
    out = []
    for r in routes:
        if hasattr(r, "effective_candidates"):
            out.extend(_flatten(r.effective_candidates()))
        elif isinstance(r, APIRoute) or isinstance(getattr(r, "original_route", None), APIRoute):
            out.append(r)
    return out


def _routes(app) -> list[tuple[str, APIRoute]]:
    return [
        (f"{method} {route.path}", route)
        for route in _flatten(app.routes)
        if route.include_in_schema
        for method in sorted(route.methods - {"HEAD", "OPTIONS"})
    ]


def collect_violations(app, spec: dict) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {c: set() for c in CATEGORIES}
    tag_names = {t["name"] for t in TAGS}
    seen_ids: dict[str, str] = {}
    for key, route in _routes(app):
        if not route.summary:
            out["summary"].add(key)
        rc = route.response_class
        rc = rc.value if isinstance(rc, DefaultPlaceholder) else rc
        streams = not issubclass(rc, JSONResponse)
        if route.response_model is None and route.status_code != 204 and not streams:
            out["response_model"].add(key)
        if not _documented_errors(route) >= _required_errors(route):
            out["error_responses"].add(key)
        if not route.tags or not set(route.tags) <= tag_names:
            out["tag"].add(key)
        op_id = route.unique_id
        if op_id in seen_ids:
            out["operation_id"].add(key)
        seen_ids[op_id] = key
        if key not in PUBLIC_ROUTES and not (_dependency_names(route) & AUTH_DEPENDENCIES):
            out["auth"].add(key)
        if _is_user_route(key, route) and key not in AUTHENTICATED_ONLY and len(_required_scopes(route)) != 1:
            out["scope"].add(key)
    for name, schema in spec.get("components", {}).get("schemas", {}).items():
        if name in _SKIP_SCHEMAS:
            continue
        for prop, body in (schema.get("properties") or {}).items():
            if not body.get("description"):
                out["description"].add(f"{name}.{prop}")
    return out


def test_route_enumeration_matches_spec(app):
    """Guard: the enumerated operations equal the spec's, and are never empty."""
    keys = [k for k, _ in _routes(app)]
    spec_ops = {
        f"{m.upper()} {path}"
        for path, item in app.openapi()["paths"].items()
        for m in item
        if m in {"get", "put", "post", "delete", "patch"}
    }
    assert keys, "route enumeration found no routes"
    assert len(keys) == len(set(keys)) == len(spec_ops)
    assert set(keys) == spec_ops


def test_public_routes_are_real_and_unauthenticated(app):
    """Ratchet: every PUBLIC_ROUTES entry exists and really has no auth dependency."""
    by_key = dict(_routes(app))
    missing = sorted(PUBLIC_ROUTES - by_key.keys())
    assert not missing, f"PUBLIC_ROUTES entries that no longer exist: {missing}"
    gated = sorted(k for k in PUBLIC_ROUTES if _dependency_names(by_key[k]) & AUTH_DEPENDENCIES)
    assert not gated, f"PUBLIC_ROUTES entries that now require auth (remove them): {gated}"


@pytest.fixture(autouse=True)
def setup_db():  # noqa: D401 - overrides conftest's DB fixture; this module needs no DB
    yield


@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture(scope="module")
def spec(app):
    return app.openapi()


@pytest.fixture(scope="module")
def violations(app, spec):
    return collect_violations(app, spec)


@pytest.mark.parametrize("category", CATEGORIES)
def test_contract_ratchet(violations, category):
    allowed = set(ALLOWLIST.get(category, ()))
    found = violations[category]
    new = sorted(found - allowed)
    stale = sorted(allowed - found)
    assert not new, f"new {category} violations (fix them; do not allowlist): {new}"
    assert not stale, (
        f"{category} allowlist entries are now fixed; remove them "
        f"(`make openapi-allowlist`): {stale}"
    )


def test_every_tag_is_described():
    assert all(t.get("description") for t in TAGS)
    assert len({t["name"] for t in TAGS}) == len(TAGS)


def test_security_schemes_declared(spec):
    assert {"userJWT", "apiToken", "ingestJWT", "scannerJWT"} <= set(spec["components"]["securitySchemes"])


def test_version_comes_from_changelog(spec):
    top = next(
        line for line in (REPO_ROOT / "CHANGELOG.md").read_text().splitlines() if line.startswith("## v")
    )
    assert spec["info"]["version"] == top.split()[1].lstrip("v")


def test_websocket_events_in_components(spec):
    assert {"WsScanSnapshot", "WsScanProgress", "WsPing"} <= set(spec["components"]["schemas"])


def test_committed_spec_is_current(spec):
    assert SPEC_PATH.is_file(), "openapi.json missing; run `make openapi`"
    committed = SPEC_PATH.read_text(encoding="utf-8")
    expected = json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert committed == expected, "openapi.json is stale; run `make openapi` and commit it"


def test_authenticated_only_routes_are_real_and_unscoped(app):
    by_key = dict(_routes(app))
    assert AUTHENTICATED_ONLY <= by_key.keys()
    for key in AUTHENTICATED_ONLY:
        assert _dependency_names(by_key[key]) & {"get_principal"}, key
        assert not _required_scopes(by_key[key]), key


def test_scanner_routes_take_no_user_scope(app):
    for key, route in _routes(app):
        if SCANNER_TAG in (route.tags or []):
            assert not _required_scopes(route), key


def test_every_catalog_scope_is_used(app):
    used = {s for _, route in _routes(app) for s in _required_scopes(route)}
    assert used <= set(SCOPES), f"routes use scopes missing from the catalog: {sorted(used - set(SCOPES))}"
    unused = set(SCOPES) - used
    assert not unused, f"catalog scopes no route uses: {sorted(unused)}"


def test_role_bundles_only_contain_catalog_scopes():
    for role, scopes in ROLE_SCOPES.items():
        assert scopes <= set(SCOPES), role
    assert ROLE_SCOPES["admin"] == set(SCOPES)
    assert "data:purge" not in ROLE_SCOPES["viewer"]


def test_operations_publish_their_required_scope(app, spec):
    for key, route in _routes(app):
        scopes = _required_scopes(route)
        if not scopes:
            continue
        method, path = key.split(" ", 1)
        security = spec["paths"][path][method.lower()]["security"]
        assert {"userJWT": scopes} in security, key


def test_data_routes_depend_on_source_scoping(app):
    """A source-limited token is refused on every route that is not declared
    source-aware, so the declaration must match the data tags exactly."""
    for key, route in _routes(app):
        if SCANNER_TAG in (route.tags or []) or not _required_scopes(route):
            continue
        is_data = bool(DATA_TAGS & set(route.tags or []))
        assert _source_scoped(route) == is_data, (
            f"{key}: tags {route.tags} but source_scoped={_source_scoped(route)}"
        )


def test_error_responses_are_derived_per_route(spec):
    """No blanket error set: each route documents only what it can produce."""
    def codes(path, method):
        return {c for c in spec["paths"][path][method]["responses"] if c.isdigit() and int(c) >= 400}

    assert codes("/api/v1/scopes", "get") == {"401"}
    assert codes("/api/v1/scanner-discovery-requests/{discovery_id}/approve", "post") >= {"401", "403", "404", "409", "410", "422"}
    assert "409" not in codes("/api/v1/entries", "get")
    assert "404" in codes("/api/v1/entries", "get")  # source-scoped: out-of-scope sources answer 404


from fastapi import HTTPException as _HTTPException

from akashic import routing as _routing
from akashic.errors import ApiHTTPException as _ApiHTTPException


def _boom(code: int):
    raise _HTTPException(status_code=code)


class _Gate:
    def check(self):
        raise _ApiHTTPException(429, "slow down", code="rate_limited")


_gate = _Gate()


@pytest.fixture
def scan_this_module(monkeypatch):
    monkeypatch.setattr(_routing, "SCANNED_MODULE_PREFIXES", ("akashic", __name__))


@_routing.raises(418)
def _boom_teapot(code: int):
    raise _HTTPException(status_code=code)


def test_dynamic_status_requires_acknowledgement(scan_this_module):
    """Fail closed: a non-literal status must be acknowledged with @raises at the raising function."""
    router = _routing.ApiRouter()
    with pytest.raises(RuntimeError, match="not acknowledged.*bad -> _boom"):
        @router.get("/x")
        def bad():
            _boom(500)

    # A route-level responses= no longer acknowledges a dynamic raise.
    with pytest.raises(RuntimeError, match="not acknowledged"):
        @router.get("/x2", responses=_routing.errors(418, 500))
        def bad2():
            _boom(500)

    @router.get("/y")
    def ok():
        _boom_teapot(418)
    assert 418 in router.routes[-1].responses


def test_raises_declaration_is_validated():
    with pytest.raises(ValueError):
        _routing.raises()
    with pytest.raises(ValueError):
        _routing.raises(200)


def test_scan_has_no_depth_limit(scan_this_module, monkeypatch):
    """A raise buried 12 helpers deep is still found (and a dynamic one still fails the build)."""
    import inspect as _inspect
    import linecache

    # exec'd functions have no source; give them a real file so the scanner can read them.
    src_lines = ["from fastapi import HTTPException\n", "def f0():\n", "    raise HTTPException(status_code=418)\n"]
    for i in range(1, 12):
        src_lines += [f"def f{i}():\n", f"    f{i - 1}()\n"]
    fname = "<akashic-deep-chain>"
    linecache.cache[fname] = (len("".join(src_lines)), None, src_lines, fname)
    ns: dict = {"__name__": "akashic.fake_deep"}
    exec(compile("".join(src_lines), fname, "exec"), ns)
    for f in ns.values():
        if _inspect.isfunction(f):
            f.__module__ = "akashic.fake_deep"
    codes, bad = _routing.scan_raises(ns["f11"])
    assert codes == {418} and not bad


def test_cycles_terminate(scan_this_module):
    codes, bad = _routing.scan_raises(_cycle_a)
    assert codes == {418} and not bad


def _cycle_a():
    _cycle_b()
    raise _HTTPException(status_code=418)


def _cycle_b():
    _cycle_a()


def test_helpers_reached_through_attributes_and_subclasses_are_scanned(scan_this_module):
    router = _routing.ApiRouter()

    @router.get("/z")
    def z():
        _gate.check()

    assert 429 in router.routes[-1].responses


def test_runtime_audit_fails_closed():
    from tests.runtime_audit import audit_observed

    spec = {"paths": {"/api/v1/a/{id}": {"get": {"responses": {"200": {}, "404": {}}}},
                      "/api/v1/d": {"get": {"responses": {"200": {}, "default": {}}}}}}
    ok = ("GET", "/a/{id}", "/api/v1/a/1", 404, True)
    assert audit_observed(spec, [ok]) == []
    # undocumented status
    assert audit_observed(spec, [("GET", "/a/{id}", "/api/v1/a/1", 500, True)])
    # `default` no longer documents arbitrary statuses
    assert audit_observed(spec, [("GET", "/d", "/api/v1/d", 500, True)])
    # real route with no spec operation
    assert audit_observed(spec, [("GET", "/ghost", "/api/v1/ghost", 200, True)])
    # excluded from the schema: skipped
    assert audit_observed(spec, [("GET", "/ghost", "/api/v1/ghost", 200, False)]) == []


# ── call-graph resolution (routing._callee) ────────────────────────────────

from fastapi.responses import JSONResponse as _JSONResponse


def _returns_variable_status(code: int):
    return _JSONResponse({"detail": "x"}, status_code=code)


@_routing.raises(409)
def _returns_acknowledged(code: int):
    return _JSONResponse({"detail": "x"}, status_code=code)


class _Guard:
    def run(self):
        self._deny()

    def _deny(self):
        raise _HTTPException(status_code=423)

    @staticmethod
    def static_deny():
        raise _HTTPException(status_code=451)


class _SubGuard(_Guard):
    def _deny(self):
        raise _HTTPException(status_code=429)


class _Holder:
    def __init__(self):
        self.guard = _Guard()  # attribute assigned at runtime: the receiver type is unknown

    def go(self):
        self.guard.run()


class _External:
    def fetch(self):
        return {}.get("x")


def test_returned_response_with_variable_status_needs_raises(scan_this_module):
    codes, bad = _routing.scan_raises(_returns_variable_status)
    assert bad and not codes
    codes, bad = _routing.scan_raises(_returns_acknowledged)
    assert codes == {409} and not bad
    router = _routing.ApiRouter()
    with pytest.raises(RuntimeError, match="not acknowledged.*bad_resp -> _returns_variable_status"):
        @router.get("/vr")
        def bad_resp():
            return _returns_variable_status(500)


def test_self_calls_and_subclass_overrides_are_followed(scan_this_module):
    codes, bad = _routing.scan_raises(_Guard.run)
    assert codes == {423, 429} and not bad  # the override in _SubGuard may run instead


def test_annotated_parameters_and_local_instances_are_followed(scan_this_module):
    def with_param(g: _Guard):
        g.static_deny()

    def with_local():
        g = _Guard()
        g._deny()

    with_param.__module__ = with_local.__module__ = __name__
    assert _routing.scan_raises(with_param)[0] == {451}
    assert 423 in _routing.scan_raises(with_local)[0]


def test_unresolved_receiver_falls_back_to_method_name(scan_this_module):
    """``self.guard.run()``: ``guard`` is set in ``__init__``, so every akashic method named
    ``run`` is considered (fail closed); the codes are documented, never silently dropped."""
    codes, _ = _routing.scan_raises(_Holder.go)
    assert {423, 429} <= codes


def test_external_receivers_are_not_flagged(scan_this_module):
    """``{}.get`` / SQLAlchemy sessions are not akashic: no fallback, no false positives."""
    assert _routing.scan_raises(_External.fetch) == (set(), [])
