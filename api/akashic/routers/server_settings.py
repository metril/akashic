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

from typing import Annotated, Any

from akashic.routing import ApiRouter
from fastapi import Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field
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

router = ApiRouter(prefix="/server-settings", tags=["server-settings"])


class SettingValue(BaseModel):
    key: str = Field(description="Setting key.")
    value: Any = Field(description="Current value; for a set secret, the mask.")
    set: bool | None = Field(default=None, description="Secrets only: whether a value is stored.")


class SettingDescription(BaseModel):
    """A setting with its metadata and current value."""

    key: str = Field(description="Setting key.")
    type: str = Field(description="Value type, e.g. `bool`, `int`, `str`.")
    group: str = Field(description="Group key the setting belongs to.")
    description: str = Field(description="Human-readable explanation.")
    secret: bool = Field(description="Whether the value is a secret (returned masked).")
    restart: bool = Field(description="Whether changing it needs a restart.")
    default: Any = Field(description="Default value.")
    value: Any = Field(description="Current value; the mask for a set secret.")
    choices: list[Any] | None = Field(description="Allowed values, when constrained.")
    set: bool | None = Field(default=None, description="Secrets only: whether a value is stored.")


class SettingGroup(BaseModel):
    """A labelled group of settings."""

    key: str = Field(description="Group key.")
    label: str = Field(description="Group display label.")
    settings: list[SettingDescription] = Field(description="Settings in the group.")


class SettingsListResponse(BaseModel):
    """All server settings, grouped."""

    groups: list[SettingGroup] = Field(description="Setting groups in display order.")


class SettingPatch(BaseModel):
    value: Any = Field(default=None, description="New value, coerced to the setting's type. For a secret: `null` or the mask keeps it, an empty string clears it.")

    model_config = {"json_schema_extra": {'examples': [{'value': True}]}}


class BulkPatch(BaseModel):
    values: dict[str, Any] = Field(description="New values keyed by setting key.")

    model_config = {"json_schema_extra": {'examples': [{'values': {'browse_enforce_perms': True}}]}}


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


@router.get(
    "",
    response_model=SettingsListResponse,
    summary="List server settings",
    description=(
        "Every server setting grouped for display, each with its type, default, current value and "
        "whether changing it needs a restart. Secret values are never returned: `value` is a mask "
        "when set and empty when not, and `set` says which.\n"
        "Requires scope `settings:read`."
    ),
)
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


@router.get(
    "/{key}",
    response_model=SettingValue,
    summary="Get a server setting",
    description=(
        "One setting's current value. For a secret, `value` is the mask when set or empty when not, "
        "and `set` says which.\n"
        "Requires scope `settings:read`. Errors: 404 for an unknown key."
    ),
)
async def get_setting_endpoint(
    key: Annotated[str, Path(description="Setting key, as listed by `GET /server-settings`.", examples=['browse_enforce_perms'])],
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


@router.patch(
    "/{key}",
    response_model=SettingValue,
    summary="Change a server setting",
    description=(
        "Sets one setting. The value is coerced to the setting's type. For a secret, `null` or the "
        "mask leaves it unchanged and an empty string clears it. The change is broadcast to every "
        "worker and recorded as a `server_setting_updated` audit event (secret values "
        "redacted); settings marked as needing a restart apply after one.\n"
        "Requires scope `settings:write`. Errors: 404 for an unknown key; 422 if the value is invalid "
        "for the setting."
    ),
)
async def patch_setting(
    key: Annotated[str, Path(description="Setting key, as listed by `GET /server-settings`.", examples=['browse_enforce_perms'])],
    body: SettingPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("settings:write"),
):
    user = principal.user
    _spec(key)
    return (await _apply(db, request, user, {key: body.value}))[key]


@router.patch(
    "",
    response_model=dict[str, SettingValue],
    summary="Change several server settings",
    description=(
        "Sets many settings in one request. The request is atomic: every value is validated first and "
        "nothing is stored if any is invalid. Secret and audit semantics are as for a single change; "
        "one `server_setting_updated` event is recorded. Returns every submitted key with its new "
        "public value.\n"
        "Requires scope `settings:write`. Errors: 404 if any key is unknown; 422 if any value is "
        "invalid (the error names the key)."
    ),
)
async def patch_settings_bulk(
    body: BulkPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("settings:write"),
):
    user = principal.user
    return await _apply(db, request, user, body.values)
