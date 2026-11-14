import asyncio
import hashlib
import hmac
import json
import logging

import httpx

from akashic.models.webhook import Webhook
from akashic.services.url_guard import UnsafeURL, assert_safe_to_dispatch_async

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """Shared lazily-created client (connection reuse across dispatches)."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=10)
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def dispatch_webhooks(webhooks, payload: dict) -> None:
    """Dispatch to every webhook concurrently; one slow/failing target
    never delays or aborts the others."""
    await asyncio.gather(
        *(dispatch_webhook(w, payload) for w in webhooks),
        return_exceptions=True,
    )


async def dispatch_webhook(webhook: Webhook, payload: dict):
    # Re-validate at dispatch time as a defense-in-depth layer for any
    # webhook persisted before the SSRF guard landed (or whose DNS
    # record changed to point at a private IP after creation).
    try:
        await assert_safe_to_dispatch_async(webhook.url)
    except UnsafeURL as exc:
        logger.warning("Webhook dispatch refused for %s: %s", webhook.url, exc)
        return

    body = json.dumps(payload, sort_keys=True, default=str)
    signature = hmac.new(webhook.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    try:
        await get_client().post(
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
