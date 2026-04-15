import hashlib
import hmac
import json
import logging

import httpx

from akashic.models.webhook import Webhook

logger = logging.getLogger(__name__)


async def dispatch_webhook(webhook: Webhook, payload: dict):
    body = json.dumps(payload, sort_keys=True, default=str)
    signature = hmac.new(webhook.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                webhook.url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Akashic-Signature": signature,
                },
                timeout=10,
            )
    except Exception as exc:
        logger.warning("Webhook dispatch to %s failed: %s", webhook.url, exc)
