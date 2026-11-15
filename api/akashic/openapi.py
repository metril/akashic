"""OpenAPI metadata: tags, security schemes, version, WebSocket schemas."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.openapi.utils import get_openapi

from akashic.auth.scopes import SCOPES
from akashic.schemas.ws_events import WS_EVENT_MODELS

SCANNER_TAG = "Scanner protocol (internal)"

TAGS: list[dict[str, str]] = [
    {"name": "auth", "description": "Login (local, LDAP and OIDC single sign-on), token refresh, logout, and the scope catalog."},
    {"name": "tokens", "description": "Personal API tokens: create, narrow and revoke your own; admins manage everyone's."},
    {"name": "users", "description": "User accounts and registration."},
    {"name": "sources", "description": "Indexed storage sources: connections, scans, reachability and scanner allow-lists."},
    {"name": "hosts", "description": "Network hosts (SMB, NFS, S3) that group sources: connection settings, share discovery, reachability checks and scanner allow-lists."},
    {"name": "credential-profiles", "description": "Reusable, encrypted connection credentials."},
    {"name": "oauth", "description": "OAuth authorization flows for cloud-drive sources."},
    {"name": "scans", "description": "Trigger, cancel and inspect scans."},
    {"name": "scanners", "description": "Scanner agents: enrolment, keys, pools and per-scanner settings."},
    {"name": "search", "description": "Full-text and filtered file search."},
    {"name": "entries", "description": "Individual indexed files and directories, their content and permissions."},
    {"name": "effective-permissions", "description": "Evaluate which rights a principal holds on an entry, from its stored ACL, mode and ownership."},
    {"name": "browse", "description": "Directory-style browsing of indexed sources, with keyset pagination and per-user ACL filtering."},
    {"name": "duplicates", "description": "Duplicate-file detection and cleanup."},
    {"name": "tags", "description": "User tags on files and sources, with inheritance."},
    {"name": "analytics", "description": "Aggregated statistics over the index."},
    {"name": "storage", "description": "Storage explorer: size breakdowns by tree, type and age."},
    {"name": "dashboard", "description": "Summary data for the landing page."},
    {"name": "identities", "description": "Your filesystem identities and their per-source bindings (which drive per-user file visibility), unbound identity claims, and group membership resolution."},
    {"name": "principals", "description": "Principal (SID/UID) lookup and name resolution."},
    {"name": "access", "description": "Admin blast-radius lookups: what a principal can reach, and who can reach a file."},
    {"name": "webhooks", "description": "Outbound webhook subscriptions."},
    {"name": "purge", "description": "Administrative removal of indexed data."},
    {"name": "admin-audit", "description": "Administrative audit log."},
    {"name": "maintenance", "description": "Long-running maintenance jobs (reindex, rebuild, cleanup)."},
    {"name": "server-settings", "description": "Runtime server configuration."},
    {"name": "health", "description": "Dependency health and service status."},
    {"name": SCANNER_TAG, "description": (
        "Endpoints spoken only by scanner agents; not a public integration surface. Protocol version 2 is required "
        "(the handshake answers 426 to anything else). Flow: enrol (`POST /scanners/claim` with a claim token, or "
        "`POST /scanners/discover` and wait for approval) -> `POST /scanners/handshake` on every start -> repeat "
        "`POST /scans/lease` (and the join long-poll `GET /scanners/{scanner_id}/scans/long-poll`) -> while holding "
        "a lease, `POST /scans/{scan_id}/heartbeat`, `POST /ingest/batch` and `/ingest/content`, log posts, and for "
        "parallel scans `POST /scans/{scan_id}/work/lease|split|{unit_id}/heartbeat|complete|fail` -> "
        "`POST /scans/{scan_id}/complete`. Scanner requests are signed with an Ed25519 JWT (`kid` is the scanner id); "
        "ingest, heartbeat and log calls use the `api_jwt` ingest token returned by the lease."
    )},
]

SECURITY_SCHEMES: dict[str, dict[str, Any]] = {
    "userJWT": {
        "type": "http", "scheme": "bearer", "bearerFormat": "JWT",
        "description": (
            "Short-lived access token from `POST /api/v1/users/login` or `/api/v1/auth/refresh`. "
            "Each operation lists the scope it requires; see `GET /api/v1/scopes`."
        ),
        "x-scopes": SCOPES,
    },
    "apiToken": {
        "type": "http", "scheme": "bearer",
        "description": "Long-lived personal API token, sent as `Authorization: Bearer <token>`.",
    },
    "ingestJWT": {
        "type": "http", "scheme": "bearer", "bearerFormat": "JWT",
        "description": "Ingest-scoped token leased to a scanner for one scan. Internal.",
    },
    "scannerJWT": {
        "type": "http", "scheme": "bearer", "bearerFormat": "JWT",
        "description": "Per-request JWT signed with the scanner's Ed25519 key. Internal.",
    },
}

_HEADING = re.compile(r"^##\s+v?(\d+\.\d+\.\d+\S*)", re.MULTILINE)


def app_version() -> str:
    """Version from the top heading of CHANGELOG.md (the source of truth).

    Raises if the file is missing or has no `## vX.Y.Z` heading: a wrong
    version in the published spec is worse than a loud failure.
    """
    here = Path(__file__).resolve()
    for root in (here.parents[2], here.parents[1]):  # repo root, or /app in the image
        changelog = root / "CHANGELOG.md"
        if changelog.is_file():
            m = _HEADING.search(changelog.read_text(encoding="utf-8"))
            if m is None:
                raise RuntimeError(f"{changelog}: no '## vX.Y.Z' heading found")
            return m.group(1)
    raise RuntimeError("CHANGELOG.md not found next to the api package or at the repo root")


def operation_id(route: APIRoute) -> str:
    return route.name


def install_openapi(app: FastAPI) -> None:
    def _build() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title, version=app.version, description=app.description,
            routes=app.routes, tags=app.openapi_tags,
        )
        components = schema.setdefault("components", {})
        components.setdefault("securitySchemes", {}).update(SECURITY_SCHEMES)
        schemas = components.setdefault("schemas", {})
        for model in WS_EVENT_MODELS:
            js = model.model_json_schema(ref_template="#/components/schemas/{model}")
            for name, sub in js.pop("$defs", {}).items():
                schemas.setdefault(name, sub)
            schemas[model.__name__] = js
        app.openapi_schema = schema
        return schema

    app.openapi = _build  # type: ignore[method-assign]
