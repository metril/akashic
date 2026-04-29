"""Phase 14c — unit tests for the SMB+SID SAMR resolver path.

These tests don't spawn a real scanner binary; they monkeypatch
`group_resolver._run_scanner` (the subprocess indirection) to return canned
CompletedProcess instances, plus `_scanner_binary_path` to inject a fake
path that makes the resolver think the binary exists.
"""
import json
import subprocess
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
    return SimpleNamespace(type="smb", connection_config=cfg)


def _binding(identifier="S-1-5-21-1-2-3-1013", id_type="sid"):
    return SimpleNamespace(identifier=identifier, identity_type=id_type)


def _proc(stdout="", stderr="", rc=0):
    return subprocess.CompletedProcess(
        args=[], returncode=rc, stdout=stdout, stderr=stderr,
    )


def _patch_binary(monkeypatch, path="/usr/local/bin/akashic-scanner"):
    monkeypatch.setattr(group_resolver, "_scanner_binary_path", lambda: path)


# ── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_samr_happy_path(monkeypatch):
    captured_argv = []

    def _fake_run(argv, timeout=30):
        captured_argv.extend(argv)
        return _proc(stdout=json.dumps({"groups": ["users", "wheel"], "source": "samr"}))

    _patch_binary(monkeypatch)
    monkeypatch.setattr(group_resolver, "_run_scanner", _fake_run)

    src = _src(host="dc.example", username="admin", password="hunter2")
    res = await resolve_groups(src, _binding())

    assert res.source == "samr"
    assert res.groups == ["users", "wheel"]
    # Verify we built the right CLI invocation.
    assert "resolve-groups" in captured_argv
    assert "--type=smb" in captured_argv
    assert "S-1-5-21-1-2-3-1013" in captured_argv


@pytest.mark.asyncio
async def test_samr_uses_default_port_445(monkeypatch):
    captured = {}

    def _fake_run(argv, timeout=30):
        captured["argv"] = argv
        return _proc(stdout=json.dumps({"groups": [], "source": "samr"}))

    _patch_binary(monkeypatch)
    monkeypatch.setattr(group_resolver, "_run_scanner", _fake_run)

    src = _src(host="h", username="u")  # no port → defaults to 445
    await resolve_groups(src, _binding())
    assert "445" in captured["argv"]


@pytest.mark.asyncio
async def test_samr_missing_host(monkeypatch):
    _patch_binary(monkeypatch)
    src = _src(username="u")
    with pytest.raises(UnsupportedResolution):
        await resolve_groups(src, _binding())


@pytest.mark.asyncio
async def test_samr_missing_username(monkeypatch):
    _patch_binary(monkeypatch)
    src = _src(host="h")
    with pytest.raises(UnsupportedResolution):
        await resolve_groups(src, _binding())


@pytest.mark.asyncio
async def test_samr_no_binary(monkeypatch):
    monkeypatch.setattr(group_resolver, "_scanner_binary_path", lambda: None)
    src = _src(host="h", username="u")
    with pytest.raises(UnsupportedResolution):
        await resolve_groups(src, _binding())


@pytest.mark.asyncio
async def test_samr_non_sid_identifier_rejected(monkeypatch):
    _patch_binary(monkeypatch)
    monkeypatch.setattr(
        group_resolver, "_run_scanner",
        lambda argv, timeout=30: pytest.fail("scanner should not be invoked"),
    )
    src = _src(host="h", username="u")
    with pytest.raises(ResolutionFailed) as ei:
        await resolve_groups(src, _binding(identifier="alice"))
    assert ei.value.reason == "not_found"


@pytest.mark.asyncio
async def test_samr_user_not_found_exits_2(monkeypatch):
    """Scanner exit code 2 → not_found."""
    _patch_binary(monkeypatch)
    monkeypatch.setattr(
        group_resolver, "_run_scanner",
        lambda argv, timeout=30: _proc(stderr="user not found in domain", rc=2),
    )
    src = _src(host="h", username="u")
    with pytest.raises(ResolutionFailed) as ei:
        await resolve_groups(src, _binding())
    assert ei.value.reason == "not_found"


@pytest.mark.asyncio
async def test_samr_scanner_failure_exits_1(monkeypatch):
    _patch_binary(monkeypatch)
    monkeypatch.setattr(
        group_resolver, "_run_scanner",
        lambda argv, timeout=30: _proc(stderr="connection refused", rc=1),
    )
    src = _src(host="h", username="u")
    with pytest.raises(ResolutionFailed) as ei:
        await resolve_groups(src, _binding())
    assert ei.value.reason == "backend_error"


@pytest.mark.asyncio
async def test_samr_bad_json(monkeypatch):
    _patch_binary(monkeypatch)
    monkeypatch.setattr(
        group_resolver, "_run_scanner",
        lambda argv, timeout=30: _proc(stdout="not json", rc=0),
    )
    src = _src(host="h", username="u")
    with pytest.raises(ResolutionFailed) as ei:
        await resolve_groups(src, _binding())
    assert ei.value.reason == "backend_error"


@pytest.mark.asyncio
async def test_samr_timeout(monkeypatch):
    _patch_binary(monkeypatch)

    def _raise(argv, timeout=30):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    monkeypatch.setattr(group_resolver, "_run_scanner", _raise)
    src = _src(host="h", username="u")
    with pytest.raises(ResolutionFailed) as ei:
        await resolve_groups(src, _binding())
    assert ei.value.reason == "backend_error"


@pytest.mark.asyncio
async def test_samr_spawn_failure(monkeypatch):
    _patch_binary(monkeypatch)

    def _raise(argv, timeout=30):
        raise OSError("permission denied")

    monkeypatch.setattr(group_resolver, "_run_scanner", _raise)
    src = _src(host="h", username="u")
    with pytest.raises(ResolutionFailed) as ei:
        await resolve_groups(src, _binding())
    assert ei.value.reason == "backend_error"
