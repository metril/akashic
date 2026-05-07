"""SSRF guard for webhook URLs (review C4).

Two layers:
1. Schema validator rejects URLs at create-time.
2. Dispatcher re-validates and refuses to fire if a stored URL turns
   out to point somewhere unsafe (e.g. DNS record changed).
"""
from unittest.mock import AsyncMock, patch

import pytest

from akashic.models.webhook import Webhook
from akashic.services.url_guard import (
    UnsafeURL,
    validate_outbound_url,
)
from akashic.services.webhooks import dispatch_webhook


# ───── unit tests for validate_outbound_url ─────


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://localhost/x",
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "http://172.16.0.1/x",
        "http://169.254.169.254/latest/meta-data",  # AWS/GCP metadata
        "http://[::1]/x",
        "http://[fe80::1]/x",
        "file:///etc/passwd",
        "gopher://internal/",
        "ftp://example.com/",
        "ws://10.0.0.1/x",
    ],
)
def test_validate_blocks_dangerous_urls(url):
    with pytest.raises(UnsafeURL):
        validate_outbound_url(url)


def test_validate_blocks_url_with_no_host():
    with pytest.raises(UnsafeURL):
        validate_outbound_url("http:///path")


def test_validate_blocks_unresolvable_host():
    with pytest.raises(UnsafeURL):
        validate_outbound_url("http://this-host-should-not-exist-akashic.invalid/")


def test_validate_blocks_hostname_resolving_to_private_ip():
    """If the hostname resolves to a private IP, it's blocked even if
    the URL doesn't contain the IP literal."""
    with patch(
        "akashic.services.url_guard._resolve_all",
        return_value=["10.0.0.1"],
    ):
        with pytest.raises(UnsafeURL):
            validate_outbound_url("http://hooks.example.com/")


def test_validate_accepts_real_public_host():
    """Public DNS that resolves to a routable IP passes."""
    with patch(
        "akashic.services.url_guard._resolve_all",
        return_value=["1.1.1.1"],
    ):
        assert validate_outbound_url("https://hooks.example.com/x") == "https://hooks.example.com/x"


# ───── integration: schema validator ─────


@pytest.mark.asyncio
async def test_create_webhook_rejects_loopback(client):
    """The POST /api/webhooks endpoint surfaces the validator failure
    as a 422 (pydantic ValidationError → FastAPI auto-422)."""
    register = await client.post(
        "/api/users/register",
        json={"username": "alice", "password": "testpass123"},
    )
    assert register.status_code == 201
    login = await client.post(
        "/api/users/login",
        json={"username": "alice", "password": "testpass123"},
    )
    token = login.json()["access_token"]
    r = await client.post(
        "/api/webhooks",
        json={
            "event_type": "scan_completed",
            "url": "http://127.0.0.1/whatever",
            "secret": "shh",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
    assert "blocked" in str(r.json()).lower() or "127.0.0.1" in str(r.json())


# ───── dispatcher defense-in-depth ─────


@pytest.mark.asyncio
async def test_dispatch_skips_unsafe_url():
    """If a webhook somehow contains a private-IP URL (older row,
    bypassed validator, DNS rebind), the dispatcher refuses to fire."""
    wh = Webhook(
        id=None,
        user_id=None,
        event_type="x",
        url="http://10.0.0.1/hook",
        secret="s",
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
        await dispatch_webhook(wh, {"hello": "world"})
        post.assert_not_called()
