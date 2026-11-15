"""OpenAPI metadata: tags, security schemes, version, WebSocket schemas."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.openapi.utils import get_openapi

from akashic.schemas.ws_events import WS_EVENT_MODELS

SCANNER_TAG = "Scanner protocol (internal)"

TAGS: list[dict[str, str]] = [
    {"name": "auth", "description": "Login, token refresh, logout and OIDC single sign-on."},
    {"name": "users", "description": "User accounts and registration."},
    {"name": "sources", "description": "Indexed storage sources: connections, scans, reachability and scanner allow-lists."},
    {"name": "hosts", "description": "Hosts that group sources, and share discovery on them."},
    {"name": "credential-profiles", "description": "Reusable, encrypted connection credentials."},
    {"name": "oauth", "description": "OAuth authorization flows for cloud-drive sources."},
    {"name": "scans", "description": "Trigger, cancel and inspect scans."},
    {"name": "scanners", "description": "Scanner agents: enrolment, keys, pools and per-scanner settings."},
    {"name": "search", "description": "Full-text and filtered file search."},
    {"name": "entries", "description": "Individual indexed files and directories, their content and permissions."},
    {"name": "effective-permissions", "description": "Effective access of an identity on an entry."},
    {"name": "browse", "description": "Directory-style browsing of indexed sources."},
    {"name": "duplicates", "description": "Duplicate-file detection and cleanup."},
    {"name": "tags", "description": "User tags on files and sources, with inheritance."},
    {"name": "analytics", "description": "Aggregated statistics over the index."},
    {"name": "storage", "description": "Storage explorer: size breakdowns by tree, type and age."},
    {"name": "dashboard", "description": "Summary data for the landing page."},
    {"name": "identities", "description": "Resolved users and groups, and group membership resolution."},
    {"name": "principals", "description": "Principal (SID/UID) lookup and name resolution."},
    {"name": "access", "description": "Who can access what: blast-radius and access reports."},
    {"name": "webhooks", "description": "Outbound webhook subscriptions."},
    {"name": "purge", "description": "Administrative removal of indexed data."},
    {"name": "admin-audit", "description": "Administrative audit log."},
    {"name": "maintenance", "description": "Long-running maintenance jobs (reindex, rebuild, cleanup)."},
    {"name": "server-settings", "description": "Runtime server configuration."},
    {"name": "health", "description": "Dependency health and service status."},
    {"name": SCANNER_TAG, "description": "Endpoints spoken only by scanner agents (ingest, leases, heartbeats, discovery). Not a public integration surface."},
]

SECURITY_SCHEMES: dict[str, dict[str, Any]] = {
    "userJWT": {
        "type": "http", "scheme": "bearer", "bearerFormat": "JWT",
        "description": "Short-lived access token from `POST /api/auth/login` or `/api/auth/refresh`.",
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
    """Version from the top heading of CHANGELOG.md, else ``APP_VERSION``, else ``dev``."""
    here = Path(__file__).resolve()
    for root in (here.parents[2], here.parents[1]):
        changelog = root / "CHANGELOG.md"
        if changelog.is_file():
            m = _HEADING.search(changelog.read_text(encoding="utf-8"))
            if m:
                return m.group(1)
    return os.getenv("APP_VERSION", "dev")


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
