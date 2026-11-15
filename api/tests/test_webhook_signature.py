"""The X-Akashic-Signature header is the HMAC-SHA256 of the raw request body as delivered."""
import hashlib
import hmac
import uuid

import httpx
import pytest

from akashic.models.webhook import Webhook
from akashic.services import webhooks as svc


class _Capture:
    def __init__(self):
        self.request: httpx.Request | None = None

    async def post(self, url, *, content, headers, timeout):
        self.request = httpx.Request("POST", url, content=content, headers=headers)  # what goes on the wire


@pytest.mark.asyncio
async def test_signature_verifies_against_raw_delivered_body(monkeypatch):
    cap = _Capture()
    monkeypatch.setattr(svc, "get_client", lambda: cap)

    async def _ok(url):
        return None

    monkeypatch.setattr(svc, "assert_safe_to_dispatch_async", _ok)
    hook = Webhook(id=uuid.uuid4(), user_id=uuid.uuid4(), event_type="scan.completed",
                   url="https://hooks.example.com/x", secret="s3crét")
    await svc.dispatch_webhook(hook, {"source_id": "b", "event": "scan.completed", "scan_id": "ünicode"})

    raw = cap.request.read()
    expected = hmac.new("s3crét".encode(), raw, hashlib.sha256).hexdigest()
    assert cap.request.headers["X-Akashic-Signature"] == expected
    assert raw.index(b'"event"') < raw.index(b'"scan_id"') < raw.index(b'"source_id"')  # keys sorted
