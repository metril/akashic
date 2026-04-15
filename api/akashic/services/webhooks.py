import hashlib
import hmac

import httpx

from akashic.models.webhook import Webhook


async def dispatch_webhook(webhook: Webhook, payload: dict):
    signature = hmac.new(webhook.secret.encode(), str(payload).encode(), hashlib.sha256).hexdigest()
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                webhook.url,
                json=payload,
                headers={"X-Akashic-Signature": signature},
                timeout=10,
            )
    except Exception:
        pass
