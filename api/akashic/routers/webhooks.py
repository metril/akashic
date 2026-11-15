import uuid

from akashic.routing import ApiRouter
from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, require_scope
from akashic.database import get_db
from akashic.models.user import User
from akashic.models.webhook import Webhook
from akashic.services.url_guard import UnsafeURL, assert_safe_to_dispatch_async
from akashic.schemas.webhook import WebhookCreate, WebhookResponse

router = ApiRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("", response_model=WebhookResponse, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    data: WebhookCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("webhooks:write"),
):
    user = principal.user
    try:
        await assert_safe_to_dispatch_async(data.url)
    except UnsafeURL as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    webhook = Webhook(
        user_id=user.id,
        event_type=data.event_type,
        url=data.url,
        secret=data.secret,
    )
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)
    return webhook


@router.get("", response_model=list[WebhookResponse])
async def list_webhooks(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("webhooks:read"),
):
    user = principal.user
    result = await db.execute(
        select(Webhook).where(Webhook.user_id == user.id).order_by(Webhook.created_at.desc())
    )
    return result.scalars().all()


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("webhooks:write"),
):
    user = principal.user
    result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.user_id == user.id)
    )
    webhook = result.scalar_one_or_none()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    await db.delete(webhook)
    await db.commit()
