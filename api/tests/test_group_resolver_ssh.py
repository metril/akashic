"""Phase 14b — unit tests for the SSH POSIX group resolver path.

The tests don't open real SSH sessions; they monkeypatch
`group_resolver._paramiko_client` to inject a MagicMock, and
`group_resolver._ssh_load_known_hosts` to a no-op (so the strict-host-key
gate passes when the test wants it to).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from akashic.services import group_resolver
from akashic.services.group_resolver import (
    ResolutionFailed,
    UnsupportedResolution,
    resolve_groups,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _src(**cfg):
    return SimpleNamespace(type="ssh", connection_config=cfg)


def _binding(identifier="1000", id_type="posix_uid"):
    return SimpleNamespace(identifier=identifier, identity_type=id_type)


def _mock_client(stdout="users wheel docker\n", stderr="", rc=0):
    """Build a paramiko-like mock that returns the canned exec output."""
    chan = MagicMock()
    chan.recv_exit_status.return_value = rc

    out = MagicMock()
    out.channel = chan
    out.read.return_value = stdout.encode()
    err = MagicMock()
    err.read.return_value = stderr.encode()

    client = MagicMock()
    client.exec_command.return_value = (MagicMock(), out, err)
    return client


def _patch_known_hosts_noop(monkeypatch):
    monkeypatch.setattr(group_resolver, "_ssh_load_known_hosts", lambda c, p: None)


# ── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ssh_happy_path(monkeypatch):
    client = _mock_client()
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: client)
    _patch_known_hosts_noop(monkeypatch)

    src = _src(host="h", port=22, username="u", known_hosts_path="/k")
    res = await resolve_groups(src, _binding())

    assert res.source == "ssh"
    assert res.groups == ["users", "wheel", "docker"]
    client.connect.assert_called_once()
    client.exec_command.assert_called_once()
    # Verifies we built the right command and quoted the identifier safely.
    cmd = client.exec_command.call_args.args[0]
    assert cmd == "id -Gn 1000"


@pytest.mark.asyncio
async def test_ssh_uses_key_when_provided(monkeypatch):
    client = _mock_client()
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: client)
    _patch_known_hosts_noop(monkeypatch)

    src = _src(
        host="h", username="u", known_hosts_path="/k",
        key_path="/path/to/key", key_passphrase="hunter2",
    )
    await resolve_groups(src, _binding())

    kwargs = client.connect.call_args.kwargs
    assert kwargs["key_filename"] == "/path/to/key"
    assert kwargs["passphrase"] == "hunter2"
    assert kwargs.get("password") is None


@pytest.mark.asyncio
async def test_ssh_uses_password_when_no_key(monkeypatch):
    client = _mock_client()
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: client)
    _patch_known_hosts_noop(monkeypatch)

    src = _src(host="h", username="u", known_hosts_path="/k", password="p")
    await resolve_groups(src, _binding())

    kwargs = client.connect.call_args.kwargs
    assert kwargs["password"] == "p"
    assert "key_filename" not in kwargs


@pytest.mark.asyncio
async def test_ssh_missing_host(monkeypatch):
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: MagicMock())
    src = _src(username="u", known_hosts_path="/k")  # no host
    with pytest.raises(UnsupportedResolution):
        await resolve_groups(src, _binding())


@pytest.mark.asyncio
async def test_ssh_missing_username(monkeypatch):
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: MagicMock())
    src = _src(host="h", known_hosts_path="/k")  # no username
    with pytest.raises(UnsupportedResolution):
        await resolve_groups(src, _binding())


@pytest.mark.asyncio
async def test_ssh_missing_known_hosts_refuses(monkeypatch):
    """Strict-by-default: without known_hosts_path, refuse to connect."""
    client = MagicMock()
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: client)
    src = _src(host="h", username="u")  # no known_hosts_path
    with pytest.raises(UnsupportedResolution):
        await resolve_groups(src, _binding())
    # And we never even attempted to connect.
    client.connect.assert_not_called()


@pytest.mark.asyncio
async def test_ssh_known_hosts_load_failure(monkeypatch):
    """If known_hosts file missing/unreadable, surfaces backend_error."""
    client = MagicMock()
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: client)
    # Real loader (no monkeypatch override) — it will FileNotFoundError.
    src = _src(host="h", username="u", known_hosts_path="/nope/does/not/exist")
    with pytest.raises(ResolutionFailed) as ei:
        await resolve_groups(src, _binding())
    assert ei.value.reason == "backend_error"


@pytest.mark.asyncio
async def test_ssh_non_numeric_identifier_is_rejected(monkeypatch):
    """Anything other than a bare integer uid is refused — no SSH attempted."""
    client = MagicMock()
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: client)
    _patch_known_hosts_noop(monkeypatch)

    src = _src(host="h", username="u", known_hosts_path="/k")
    with pytest.raises(ResolutionFailed) as ei:
        await resolve_groups(src, _binding(identifier="; rm -rf /"))
    assert ei.value.reason == "not_found"
    client.connect.assert_not_called()


@pytest.mark.asyncio
async def test_ssh_connect_failure(monkeypatch):
    client = MagicMock()
    client.connect.side_effect = OSError("conn refused")
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: client)
    _patch_known_hosts_noop(monkeypatch)

    src = _src(host="h", username="u", known_hosts_path="/k")
    with pytest.raises(ResolutionFailed) as ei:
        await resolve_groups(src, _binding())
    assert ei.value.reason == "backend_error"


@pytest.mark.asyncio
async def test_ssh_id_no_such_user(monkeypatch):
    client = _mock_client(stdout="", stderr="id: '999999': no such user", rc=1)
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: client)
    _patch_known_hosts_noop(monkeypatch)

    src = _src(host="h", username="u", known_hosts_path="/k")
    with pytest.raises(ResolutionFailed) as ei:
        await resolve_groups(src, _binding(identifier="999999"))
    assert ei.value.reason == "not_found"


@pytest.mark.asyncio
async def test_ssh_id_other_failure(monkeypatch):
    client = _mock_client(stdout="", stderr="permission denied", rc=2)
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: client)
    _patch_known_hosts_noop(monkeypatch)

    src = _src(host="h", username="u", known_hosts_path="/k")
    with pytest.raises(ResolutionFailed) as ei:
        await resolve_groups(src, _binding())
    assert ei.value.reason == "backend_error"


@pytest.mark.asyncio
async def test_ssh_client_always_closes(monkeypatch):
    """Even on connect failure the client.close() is called."""
    client = MagicMock()
    client.connect.side_effect = RuntimeError("boom")
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: client)
    _patch_known_hosts_noop(monkeypatch)

    src = _src(host="h", username="u", known_hosts_path="/k")
    with pytest.raises(ResolutionFailed):
        await resolve_groups(src, _binding())
    client.close.assert_called_once()


@pytest.mark.asyncio
async def test_ssh_empty_groups_list(monkeypatch):
    """`id -Gn` with empty stdout → empty groups list, not an error."""
    client = _mock_client(stdout="\n", rc=0)
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: client)
    _patch_known_hosts_noop(monkeypatch)

    src = _src(host="h", username="u", known_hosts_path="/k")
    res = await resolve_groups(src, _binding())
    assert res.groups == []
    assert res.source == "ssh"
