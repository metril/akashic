"""Admin API for runtime server settings (typed registry, grouped).

GET  /api/v1/server-settings        -> {"groups": [{key,label,settings:[...]}]}
GET  /api/v1/server-settings/{key}  -> {key, value, set}
PATCH /api/v1/server-settings/{key} body {"value": ...}
PATCH /api/v1/server-settings       body {"values": {key: value, ...}} (atomic)

Secrets are never returned: ``value`` is the mask when set, "" when not,
and ``set`` says which. On PATCH a secret of null or the mask is left
unchanged; an explicit "" clears it.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, require_scope
from akashic.database import get_db
from akashic.models.server_setting import ServerSetting
from akashic.models.user import User
from akashic.services import scan_pubsub
from akashic.services.audit import record_event
from akashic.services.runtime_settings import (
    GROUPS, MASK, REGISTRY, Spec, coerce, decode_stored, encode_stored,
    load_snapshot,
)
from akashic.services.server_settings import set_setting

router = APIRouter(prefix="/server-settings", tags=["server-settings"])


class SettingValue(BaseModel):
    key: str
    value: Any
    set: bool | None = None  # secrets only


class SettingPatch(BaseModel):
    value: Any = None


class BulkPatch(BaseModel):
    values: dict[str, Any]


def _public(spec: Spec, stored: Any, present: bool) -> tuple[Any, bool | None]:
    """(value, set) as exposed to clients."""
    if spec.secret:
        is_set = bool(stored)
        return (MASK if is_set else ""), is_set
    if not present:
        return spec.default, None
    try:
        return decode_stored(spec, stored), None
    except ValueError:
        return spec.default, None


def _describe(spec: Spec, stored: Any, present: bool) -> dict:
    value, is_set = _public(spec, stored, present)
    d = {
        "key": spec.key, "type": spec.type_name, "group": spec.group,
        "description": spec.description, "secret": spec.secret,
        "restart": spec.restart, "default": spec.default, "value": value,
        "choices": list(spec.choices) if spec.choices else None,
    }
    if spec.secret:
        d["set"] = is_set
    return d


def _invalid(key: str, msg: str) -> HTTPException:
    """FastAPI-style 422 so clients can map the error to the field."""
    return HTTPException(422, [{
        "type": "value_error", "loc": ["body", "values", key], "msg": msg,
    }])


def _prepare(spec: Spec, raw: Any) -> tuple[bool, Any]:
    """(changed, value_to_store). Raises HTTPException(422) on bad input."""
    if spec.secret:
        if raw is None or raw == MASK:
            return False, None
        if not isinstance(raw, str):
            raise _invalid(spec.key, "must be a string")
        return True, encode_stored(spec, raw)
    try:
        return True, coerce(spec, raw)
    except ValueError as e:
        raise _invalid(spec.key, str(e)) from e


def _spec(key: str) -> Spec:
    spec = REGISTRY.get(key)
    if spec is None:
        raise HTTPException(404, f"unknown setting: {key}")
    return spec


async def _rows(db: AsyncSession) -> dict[str, Any]:
    return {r.key: r.value for r in (await db.execute(select(ServerSetting))).scalars()}


@router.get("")
async def list_settings(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("settings:read"),
):
    rows = await _rows(db)
    groups = []
    for gkey, label in GROUPS:
        groups.append({
            "key": gkey, "label": label,
            "settings": [
                _describe(s, rows.get(s.key), s.key in rows)
                for s in REGISTRY.values() if s.group == gkey
            ],
        })
    return {"groups": groups}


@router.get("/{key}", response_model=SettingValue)
async def get_setting_endpoint(
    key: str,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("settings:read"),
):
    spec = _spec(key)
    rows = await _rows(db)
    value, is_set = _public(spec, rows.get(key), key in rows)
    return SettingValue(key=key, value=value, set=is_set)


async def _apply(
    db: AsyncSession, request: Request, user: User, items: dict[str, Any],
) -> dict[str, SettingValue]:
    prepared: dict[str, Any] = {}
    for key, raw in items.items():
        spec = _spec(key)
        changed, val = _prepare(spec, raw)
        if changed:
            prepared[key] = val
    for key, val in prepared.items():
        await set_setting(db, key, val, user_id=user.id)
    await db.commit()
    await load_snapshot(db)
    if prepared:
        # Bust cache + reload snapshot on every worker/process.
        await scan_pubsub.publish_scanner_event({
            "kind": "setting.changed",
            "key": next(iter(prepared)) if len(prepared) == 1 else None,
        })
        await record_event(
            db=db, user=user, event_type="server_setting_updated",
            request=request,
            payload={"values": {
                k: ("<redacted>" if REGISTRY[k].secret else v)
                for k, v in prepared.items()
            }, "key": next(iter(prepared)) if len(prepared) == 1 else None},
        )
    rows = await _rows(db)
    out = {}
    for key in items:
        v, s = _public(REGISTRY[key], rows.get(key), key in rows)
        out[key] = SettingValue(key=key, value=v, set=s)
    return out


@router.patch("/{key}", response_model=SettingValue)
async def patch_setting(
    key: str,
    body: SettingPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("settings:write"),
):
    user = principal.user
    _spec(key)
    return (await _apply(db, request, user, {key: body.value}))[key]


@router.patch("", response_model=dict[str, SettingValue])
async def patch_settings_bulk(
    body: BulkPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("settings:write"),
):
    user = principal.user
    return await _apply(db, request, user, body.values)
