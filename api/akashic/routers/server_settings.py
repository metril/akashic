"""Admin GET/PATCH for runtime server settings.

The values themselves are intentionally typed as `Any` (JSONB) so
the same endpoint handles every flavour of toggle. Callers that
care about a specific shape (e.g. the discovery endpoint reading a
bool) validate at the read site, not here.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import require_admin
from akashic.database import get_db
from akashic.models.server_setting import ServerSetting
from akashic.models.user import User
from akashic.services import scan_pubsub
from akashic.services.audit import record_event
from akashic.services.server_settings import set_setting

router = APIRouter(prefix="/api/server-settings", tags=["server-settings"])


class SettingValue(BaseModel):
    key: str
    value: Any


class SettingPatch(BaseModel):
    value: Any


@router.get("", response_model=list[SettingValue])
async def list_settings(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    rows = (await db.execute(select(ServerSetting))).scalars().all()
    return [SettingValue(key=r.key, value=r.value) for r in rows]


@router.get("/{key}", response_model=SettingValue)
async def get_setting_endpoint(
    key: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    row = (await db.execute(
        select(ServerSetting).where(ServerSetting.key == key)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown setting: {key}")
    return SettingValue(key=row.key, value=row.value)


@router.patch("/{key}", response_model=SettingValue)
async def patch_setting(
    key: str,
    body: SettingPatch,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    row = await set_setting(db, key, body.value, user_id=user.id)
    await db.commit()
    # Bust cache cluster-wide so toggling on one node propagates
    # without waiting on the 5s TTL.
    await scan_pubsub.publish_scanner_event({
        "kind": "setting.changed",
        "key": key,
    })
    await record_event(
        db=db, user=user, event_type="server_setting_updated",
        request=request,
        payload={"key": key, "value": body.value},
    )
    return SettingValue(key=row.key, value=row.value)
