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
from akashic.openapi import TAGS
from akashic.schemas.common import ERROR_RESPONSES, ErrorResponse
from tests.openapi_allowlist import ALLOWLIST

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "openapi.json"

# Dependencies that authenticate the caller (directly or transitively).
AUTH_DEPENDENCIES = frozenset({
    "get_current_user", "require_admin", "get_ingest_user",
    "get_ingest_scanner_id", "verify_scanner_jwt",
})

# Routes that are intentionally unauthenticated: they mint credentials,
# bootstrap a scanner, or are browser redirects.
PUBLIC_ROUTES = frozenset({
    "GET /api/auth/providers",
    "GET /api/auth/oidc/login",
    "GET /api/auth/oidc/callback",
    "POST /api/auth/ldap/login",
    "POST /api/auth/refresh",
    "POST /api/auth/logout",
    "POST /api/users/register",
    "POST /api/users/login",
    "POST /api/scanners/claim",
    "POST /api/scanners/discover",
    "GET /api/scanners/discover/{discovery_id}",
    "GET /api/oauth/callback",
})

# Built-in FastAPI schemas we don't author.
_SKIP_SCHEMAS = frozenset({"HTTPValidationError", "ValidationError"})

CATEGORIES = ("summary", "response_model", "error_responses", "tag", "operation_id", "auth", "description")


def _dependency_names(route: APIRoute) -> set[str]:
    names: set[str] = set()

    def walk(dependant) -> None:
        for sub in dependant.dependencies:
            names.add(getattr(sub.call, "__name__", ""))
            walk(sub)

    walk(route.dependant)
    return names


def _routes(app) -> list[tuple[str, APIRoute]]:
    return [
        (f"{method} {route.path}", route)
        for route in app.routes
        if isinstance(route, APIRoute) and route.include_in_schema
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
        if not all(
            code in route.responses and route.responses[code].get("model") is ErrorResponse
            for code in ERROR_RESPONSES
        ):
            out["error_responses"].add(key)
        if not route.tags or not set(route.tags) <= tag_names:
            out["tag"].add(key)
        op_id = route.unique_id
        if op_id in seen_ids:
            out["operation_id"].add(key)
        seen_ids[op_id] = key
        if key not in PUBLIC_ROUTES and not (_dependency_names(route) & AUTH_DEPENDENCIES):
            out["auth"].add(key)
    for name, schema in spec.get("components", {}).get("schemas", {}).items():
        if name in _SKIP_SCHEMAS:
            continue
        for prop, body in (schema.get("properties") or {}).items():
            if not body.get("description"):
                out["description"].add(f"{name}.{prop}")
    return out


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
