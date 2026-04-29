"""Phase B1 — tests for POST /api/sources/test and the source_tester service.

The scanner subprocess is mocked via _run_scanner. Local-path probes hit
the actual filesystem (using tmp_path to keep things hermetic).
"""
from __future__ import annotations

import os
import subprocess
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.audit_event import AuditEvent
from akashic.models.user import User
from akashic.services import source_tester

# pytest auto-collects functions starting with `test_`, so we deliberately
# don't name-import test_local / test_connection — go through the module.
_test_local = source_tester.test_local
_test_connection = source_tester.test_connection


# ── source_tester unit tests ────────────────────────────────────────────────


def test_local_happy_path(tmp_path):
    res = _test_local({"path": str(tmp_path)})
    assert res.ok is True
    assert res.step is None


def test_local_missing_path():
    res = _test_local({})
    assert res.ok is False
    assert res.step == "config"


def test_local_not_a_directory(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    res = _test_local({"path": str(f)})
    assert res.ok is False
    assert res.step == "list"


def test_local_unreadable(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root bypasses permission bits")
    p = tmp_path / "restricted"
    p.mkdir()
    p.chmod(0o000)
    try:
        res = _test_local({"path": str(p)})
        assert res.ok is False
        assert res.step == "list"
    finally:
        p.chmod(0o755)


def test_dispatch_unknown_type():
    res = _test_connection("not-a-type", {})
    assert res.ok is False
    assert res.step == "config"


def test_dispatch_local_routes_to_local(tmp_path):
    res = _test_connection("local", {"path": str(tmp_path)})
    assert res.ok is True


def test_ssh_requires_known_hosts(monkeypatch):
    # Should never reach scanner — config check rejects first.
    monkeypatch.setattr(source_tester, "_scanner_binary_path", lambda: "/fake")
    monkeypatch.setattr(
        source_tester, "_run_scanner",
        lambda *a, **kw: pytest.fail("scanner should not be invoked"),
    )
    res = _test_connection("ssh", {"host": "h", "username": "u"})
    assert res.ok is False
    assert res.step == "config"
    assert "known_hosts" in res.error


def test_ssh_happy_path(monkeypatch):
    monkeypatch.setattr(source_tester, "_scanner_binary_path", lambda: "/fake")
    captured = {}

    def _fake(argv, password="", key_passphrase="", timeout=15):
        captured["argv"] = argv
        captured["password"] = password
        captured["key_passphrase"] = key_passphrase
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout='{"ok":true}\n', stderr="")

    monkeypatch.setattr(source_tester, "_run_scanner", _fake)
    res = _test_connection("ssh", {
        "host": "h", "username": "u", "password": "secret",
        "known_hosts_path": "/k", "port": 2222,
    })
    assert res.ok is True
    assert "secret" not in " ".join(captured["argv"])
    assert captured["password"] == "secret"
    assert "--password-stdin" in captured["argv"]
    assert "2222" in captured["argv"]


def test_ssh_key_passphrase_routed_via_stdin_not_argv(monkeypatch):
    """key_passphrase is a credential — must never end up in /proc/<pid>/cmdline."""
    monkeypatch.setattr(source_tester, "_scanner_binary_path", lambda: "/fake")
    captured = {}

    def _fake(argv, password="", key_passphrase="", timeout=15):
        captured["argv"] = argv
        captured["key_passphrase"] = key_passphrase
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout='{"ok":true}\n', stderr="")

    monkeypatch.setattr(source_tester, "_run_scanner", _fake)
    res = _test_connection("ssh", {
        "host": "h", "username": "u",
        "key_path": "/etc/akashic/keys/id_rsa",
        "key_passphrase": "supersecret-passphrase",
        "known_hosts_path": "/k",
    })
    assert res.ok is True
    # The passphrase must be piped via stdin, not argv.
    assert captured["key_passphrase"] == "supersecret-passphrase"
    assert "supersecret-passphrase" not in " ".join(captured["argv"])
    assert "--key-passphrase" not in captured["argv"]


def test_ssh_classifies_step_from_stderr(monkeypatch):
    monkeypatch.setattr(source_tester, "_scanner_binary_path", lambda: "/fake")
    monkeypatch.setattr(
        source_tester, "_run_scanner",
        lambda argv, password="", key_passphrase="", timeout=15: subprocess.CompletedProcess(
            args=argv, returncode=1, stdout="",
            stderr="auth: NT_STATUS_LOGON_FAILURE\n",
        ),
    )
    res = _test_connection("smb", {
        "host": "h", "username": "u", "share": "s", "password": "wrong",
    })
    assert res.ok is False
    assert res.step == "auth"
    assert "LOGON_FAILURE" in res.error


def test_smb_unclassified_stderr(monkeypatch):
    """Stderr without the step:reason convention falls through with step=None."""
    monkeypatch.setattr(source_tester, "_scanner_binary_path", lambda: "/fake")
    monkeypatch.setattr(
        source_tester, "_run_scanner",
        lambda argv, password="", key_passphrase="", timeout=15: subprocess.CompletedProcess(
            args=argv, returncode=1, stdout="", stderr="something went wrong\n",
        ),
    )
    res = _test_connection("smb", {"host": "h", "username": "u", "share": "s"})
    assert res.ok is False
    assert res.step is None


def test_no_scanner_binary(monkeypatch):
    monkeypatch.setattr(source_tester, "_scanner_binary_path", lambda: None)
    res = _test_connection("ssh", {
        "host": "h", "username": "u", "known_hosts_path": "/k",
    })
    assert res.ok is False
    assert res.step == "config"


def test_scanner_timeout(monkeypatch):
    monkeypatch.setattr(source_tester, "_scanner_binary_path", lambda: "/fake")

    def _raise(argv, password="", key_passphrase="", timeout=15):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)

    monkeypatch.setattr(source_tester, "_run_scanner", _raise)
    res = _test_connection("smb", {"host": "h", "username": "u", "share": "s"})
    assert res.ok is False
    assert res.step == "connect"


def test_nfs_dispatches_tcp_probe(monkeypatch):
    monkeypatch.setattr(source_tester, "_scanner_binary_path", lambda: "/fake")
    captured = {}

    def _fake(argv, password="", key_passphrase="", timeout=15):
        captured["argv"] = argv
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout='{"ok":true}\n', stderr="")

    monkeypatch.setattr(source_tester, "_run_scanner", _fake)
    res = _test_connection("nfs", {"host": "nfs.example.com", "export_path": "/srv/data"})
    assert res.ok is True
    assert "--type=nfs" in captured["argv"]
    assert "nfs.example.com" in captured["argv"]
    assert "2049" in captured["argv"]


def test_nfs_missing_required():
    res = _test_connection("nfs", {"host": "h"})
    assert res.ok is False
    assert res.step == "config"
    assert "export_path" in res.error


def test_nfs_connect_failure_propagates(monkeypatch):
    monkeypatch.setattr(source_tester, "_scanner_binary_path", lambda: "/fake")

    def _fake(argv, password="", key_passphrase="", timeout=15):
        return subprocess.CompletedProcess(
            args=argv, returncode=1, stdout="",
            stderr="connect:dial tcp 10.0.0.1:2049: i/o timeout\n",
        )

    monkeypatch.setattr(source_tester, "_run_scanner", _fake)
    res = _test_connection("nfs", {"host": "10.0.0.1", "export_path": "/e"})
    assert res.ok is False
    assert res.step == "connect"
    assert "timeout" in res.error


def test_s3_dispatches_with_secret_in_password_field(monkeypatch):
    monkeypatch.setattr(source_tester, "_scanner_binary_path", lambda: "/fake")
    captured = {}

    def _fake(argv, password="", key_passphrase="", timeout=15):
        captured["password"] = password
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout='{"ok":true}\n', stderr="")

    monkeypatch.setattr(source_tester, "_run_scanner", _fake)
    res = _test_connection("s3", {
        "bucket": "b", "region": "us-east-1",
        "access_key_id": "AKIA...", "secret_access_key": "secret",
    })
    assert res.ok is True
    assert captured["password"] == "secret"


# ── Endpoint integration tests ──────────────────────────────────────────────


@pytest_asyncio.fixture
async def admin_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(),
            username="admin",
            email="admin@example.com",
            password_hash="x",
            role="admin",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def client(setup_db, admin_user: User) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_get_current_user():
        return admin_user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_endpoint_local_ok(client: AsyncClient, tmp_path):
    r = await client.post("/api/sources/test", json={
        "type": "local",
        "connection_config": {"path": str(tmp_path)},
    })
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "step": None, "error": None}


@pytest.mark.asyncio
async def test_endpoint_local_bad_path(client: AsyncClient):
    r = await client.post("/api/sources/test", json={
        "type": "local",
        "connection_config": {"path": "/does/not/exist"},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["step"] == "list"


@pytest.mark.asyncio
async def test_endpoint_records_audit_without_credentials(
    client: AsyncClient, setup_db, tmp_path
):
    r = await client.post("/api/sources/test", json={
        "type": "smb",
        "connection_config": {
            "host": "h", "username": "u", "share": "s",
            "password": "smb-secret-001",
            "domain": "EXAMPLE",
        },
    })
    assert r.status_code == 200

    async with setup_db() as session:  # type: AsyncSession
        result = await session.execute(
            select(AuditEvent).where(AuditEvent.event_type == "source_test_run")
        )
        events = result.scalars().all()
    assert len(events) == 1
    payload = events[0].payload
    assert payload["type"] == "smb"
    assert payload["config"]["host"] == "h"
    assert payload["config"]["share"] == "s"
    assert payload["config"]["domain"] == "EXAMPLE"
    # Allow-list invariant: no credential-shaped key may appear in config,
    # and no specific secret value may leak into the serialized payload.
    for credential_key in ("password", "key_passphrase", "secret_access_key"):
        assert credential_key not in payload["config"], (
            f"{credential_key} must not be audited"
        )
    assert "smb-secret-001" not in str(payload)


@pytest.mark.asyncio
async def test_endpoint_audit_includes_access_key_id_for_s3(
    client: AsyncClient, setup_db, monkeypatch
):
    """access_key_id is the public half of an AWS pair — auditing it lets us
    answer 'which credentials were used'. The secret half must still be
    excluded."""
    monkeypatch.setattr(source_tester, "_scanner_binary_path", lambda: None)
    r = await client.post("/api/sources/test", json={
        "type": "s3",
        "connection_config": {
            "bucket": "b", "region": "us-east-1",
            "access_key_id": "AKIAEXAMPLE001",
            "secret_access_key": "must-not-be-audited",
        },
    })
    assert r.status_code == 200

    async with setup_db() as session:  # type: AsyncSession
        result = await session.execute(
            select(AuditEvent).where(AuditEvent.event_type == "source_test_run")
        )
        events = result.scalars().all()
    assert len(events) == 1
    payload = events[0].payload
    assert payload["config"]["access_key_id"] == "AKIAEXAMPLE001"
    assert "secret_access_key" not in payload["config"]
    assert "must-not-be-audited" not in str(payload)


@pytest.mark.asyncio
async def test_endpoint_unsupported_type_returns_200_with_ok_false(client: AsyncClient):
    r = await client.post("/api/sources/test", json={
        "type": "ftp",
        "connection_config": {},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["step"] == "config"
