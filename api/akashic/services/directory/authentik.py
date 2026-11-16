"""Authentik reader: users and groups via the Authentik REST API.

Error text carries only the HTTP status/path or the exception class name,
never the token (httpx messages and response bodies can echo it).
"""
from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from akashic.services.directory.records import DirectoryError, DirRecord, NotConfigured
from akashic.services.runtime_settings import rt
from akashic.services.sid import decode_object_sid

logger = logging.getLogger(__name__)

PAGE_SIZE = 200
SERVICE_TYPES = {"service_account", "internal_service_account"}


def authentik_configured() -> bool:
    return bool(rt().authentik_url and rt().authentik_api_token)


def authentik_keys(attributes: dict) -> tuple[str | None, str | None]:
    """(sid, dn) from the attributes Authentik's LDAP source stores."""
    attributes = attributes or {}
    sid = decode_object_sid(attributes.get("objectSid")) or decode_object_sid(
        attributes.get("ldap_uniq"))
    dn = attributes.get("distinguishedName")
    return sid, dn if isinstance(dn, str) and dn else None


def _client(timeout: float) -> httpx.AsyncClient:
    if not authentik_configured():
        raise NotConfigured("Authentik is not configured")
    return httpx.AsyncClient(
        base_url=rt().authentik_url.rstrip("/"),
        timeout=timeout,
        headers={"Authorization": f"Bearer {rt().authentik_api_token}"},
    )


def _fail(msg: str) -> DirectoryError:
    logger.warning("%s", msg)
    return DirectoryError(msg)


async def _get(client: httpx.AsyncClient, path: str, params: dict | None = None) -> httpx.Response:
    try:
        resp = await client.get(path, params=params)
    except httpx.HTTPError as exc:
        raise _fail(f"Authentik: {type(exc).__name__} on {path}") from None
    if resp.status_code >= 400 and resp.status_code != 404:
        raise _fail(f"Authentik: HTTP {resp.status_code} on {path}")
    return resp


def _build(fn, d, path: str):
    try:
        return fn(d)
    except (KeyError, TypeError, AttributeError):
        raise _fail(f"Authentik: invalid response on {path}") from None


def _json(resp: httpx.Response, path: str) -> dict:
    try:
        body = resp.json()
    except ValueError:
        raise _fail(f"Authentik: invalid response on {path}") from None
    if not isinstance(body, dict):
        raise _fail(f"Authentik: invalid response on {path}")
    return body


def _person(d: dict) -> DirRecord | None:
    if d.get("type") in SERVICE_TYPES:
        return None
    sid, dn = authentik_keys(d.get("attributes") or {})
    return DirRecord(
        backend="authentik", ref=str(d["pk"]), kind="person",
        display_name=d.get("name") or None, username=d.get("username"),
        email=d.get("email") or None, sid=sid, dn=dn,
    )


def _group(d: dict) -> DirRecord:
    sid, dn = authentik_keys(d.get("attributes") or {})
    return DirRecord(
        backend="authentik", ref=str(d["pk"]), kind="group",
        display_name=d.get("name") or None, username=None, email=None,
        sid=sid, dn=dn,
    )


async def _pages(client: httpx.AsyncClient, path: str, extra: dict):
    page = 1
    while page:
        resp = await _get(client, path, {"page": page, "page_size": PAGE_SIZE, **extra})
        if resp.status_code == 404:
            raise _fail(f"Authentik: HTTP 404 on {path}")
        body = _json(resp, path)
        results = body.get("results")
        if not isinstance(results, list):
            raise _fail(f"Authentik: invalid response on {path}")
        for item in results:
            yield item
        page = (body.get("pagination") or {}).get("next") or 0


async def fetch_all_authentik(timeout: float = 30.0) -> list[DirRecord]:
    out: list[DirRecord] = []
    async with _client(timeout) as client:
        async for d in _pages(client, "/api/v3/core/users/", {}):
            rec = _build(_person, d, "/api/v3/core/users/")
            if rec is not None:
                out.append(rec)
        async for d in _pages(client, "/api/v3/core/groups/", {"include_users": "false"}):
            out.append(_build(_group, d, "/api/v3/core/groups/"))
    return out


async def fetch_authentik_person(pk: str, timeout: float = 3.0) -> DirRecord | None:
    """One user by pk. ``None`` means authoritatively not found."""
    async with _client(timeout) as client:
        path = f"/api/v3/core/users/{quote(pk, safe='')}/"
        resp = await _get(client, path)
    if resp.status_code == 404:
        return None
    return _build(_person, _json(resp, path), path)
