from typing import Annotated
import uuid

from akashic.routing import ApiRouter
from fastapi import Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import Principal, require_scope
from akashic.database import get_db
from akashic.models.user import User
from akashic.models.webhook import Webhook
from akashic.services.url_guard import UnsafeURL, assert_safe_to_dispatch_async
from akashic.schemas.webhook import WebhookCreate, WebhookResponse

router = ApiRouter(prefix="/webhooks", tags=["webhooks"])


@router.post(
    "",
    response_model=WebhookResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a webhook",
    description=(
        "Registers an outgoing webhook for the calling user. Matching events are POSTed to `url` as "
        "JSON with an `X-Akashic-Signature` header: the hex HMAC-SHA256 of the request body (compact "
        "JSON, keys sorted) keyed with `secret`. Scan webhooks (`scan.completed`, `scan.failed`) "
        "carry `event`, `scan_id` and `source_id`, and fire only for webhooks owned by users whose "
        "role holds `scans:write`.\n"
        "The URL must resolve to a public address; private, loopback and link-local targets are "
        "refused, and re-checked at delivery time.\n"
        "Requires scope `webhooks:write`. Errors: 422 for a malformed body or an unsafe URL."
    ),
)
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


@router.get(
    "",
    response_model=list[WebhookResponse],
    summary="List your webhooks",
    description=(
        "The calling user's webhooks, newest first. Webhooks are private to their owner. The `secret` "
        "is never returned.\n"
        "Requires scope `webhooks:read`."
    ),
)
async def list_webhooks(
    db: AsyncSession = Depends(get_db),
    principal: Principal = require_scope("webhooks:read"),
):
    user = principal.user
    result = await db.execute(
        select(Webhook).where(Webhook.user_id == user.id).order_by(Webhook.created_at.desc())
    )
    return result.scalars().all()


@router.delete(
    "/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a webhook",
    description=(
        "Permanently deletes one of your webhooks.\n"
        "Requires scope `webhooks:write`. Errors: 404 if the webhook does not exist or belongs to "
        "another user."
    ),
)
async def delete_webhook(
    webhook_id: Annotated[uuid.UUID, Path(description="Webhook id.")],
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
