import hashlib
import hmac
import json
import logging

import httpx

from akashic.models.webhook import Webhook
from akashic.services.url_guard import UnsafeURL, assert_safe_to_dispatch

logger = logging.getLogger(__name__)


async def dispatch_webhook(webhook: Webhook, payload: dict):
    # Re-validate at dispatch time as a defense-in-depth layer for any
    # webhook persisted before the SSRF guard landed (or whose DNS
    # record changed to point at a private IP after creation).
    try:
        assert_safe_to_dispatch(webhook.url)
    except UnsafeURL as exc:
        logger.warning("Webhook dispatch refused for %s: %s", webhook.url, exc)
        return

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
