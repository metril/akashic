# Phase 14b — Group-Membership Auto-Resolution (SSH POSIX) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add SSH POSIX group resolution to the existing `services/group_resolver.py` dispatcher. After Phase 14a, SSH+posix_uid raises `UnsupportedResolution`; after this phase it runs `id -Gn <identifier>` over SSH and returns the resolved groups.

**Out of scope (Phase 14c):** NT SMB SAMR group resolution. That requires either (a) a new Python SAMR client (impacket) or (b) a Go scanner package exposed as a CLI command — both are multi-day efforts and warrant their own plan.

**Architecture:** Extend the dispatcher to route `(source.type='ssh', identity_type='posix_uid')` to a new `_resolve_posix_ssh(source, binding)` helper. The helper opens an SSH session via `paramiko`, executes `id -Gn <identifier>` (group names, space-separated), parses the result, and returns `ResolveResult(source="ssh", ...)`. Connection details come from `source.connection_config` using the same key names the SSH connector uses (`host`, `port`, `username`, `password`, `key_path`, `key_passphrase`, `known_hosts_path`).

**Tech Stack:** Python 3.12, `paramiko` (new dependency), pytest-asyncio.

---

## File structure

**Create**
- `api/tests/test_group_resolver_ssh.py` — unit tests for the SSH path with mocked paramiko.

**Edit**
- `api/pyproject.toml` — add `paramiko>=3.4` to runtime deps.
- `api/akashic/services/group_resolver.py` — add `_ssh_client()` indirection, `_resolve_posix_ssh()`, dispatcher wiring, extend `ResolveResult.source` Literal to include `"ssh"`.

**No deletes. No new endpoints — the existing `POST /api/identities/{id}/bindings/{bid}/resolve-groups` endpoint dispatches transparently.**

---

## Cross-task spec: dispatcher table

| `source.type` | `binding.identity_type` | Implementation | Phase |
|---|---|---|---|
| `local`, `nfs` | `posix_uid` | Python `pwd`/`grp` | 14a ✅ |
| any | `nfsv4_principal` | LDAP `(uid=…) → memberOf` | 14a ✅ |
| `ssh` | `posix_uid` | **paramiko + `id -Gn`** | 14b (this) |
| `smb` | `sid` | SAMR | 14c ⛔ |
| `s3` | any | (no analog) | always ⛔ |

`ResolveResult.source` Literal grows from `"nss" | "ldap"` → `"nss" | "ldap" | "ssh"`.

`PrincipalGroupsCache` schema is unchanged (the `groups_source` column is just `str`).

The audit `groups_auto_resolved` event payload `source` field will start including `"ssh"` automatically — no schema change required (it's a JSONB payload).

---

## Task 1 — Add paramiko dependency

**Files:**
- Modify: `api/pyproject.toml`

- [ ] **Step 1: Add paramiko to runtime dependencies**

Insert `paramiko>=3.4` in the `dependencies` array. Place it alphabetically near other infra deps (after `meilisearch-python-sdk`, before `python-jose`).

- [ ] **Step 2: Rebuild the api container so the dep is available**

```
cd /home/OLYMPOS/jagannath/projects/akashic-eff5
docker compose -f docker-compose.eff5.yml build api
```

- [ ] **Step 3: Verify import works**

```
docker compose -f docker-compose.eff5.yml run --rm api python -c "import paramiko; print(paramiko.__version__)"
```

Expected: prints `3.x.x`.

---

## Task 2 — `_resolve_posix_ssh` helper

**Files:**
- Modify: `api/akashic/services/group_resolver.py`

- [ ] **Step 1: Extend `ResolveResult.source` Literal**

Replace:
```python
class ResolveResult(BaseModel):
    groups: list[str]
    source: Literal["nss", "ldap"]
    resolved_at: datetime
```

With:
```python
class ResolveResult(BaseModel):
    groups: list[str]
    source: Literal["nss", "ldap", "ssh"]
    resolved_at: datetime
```

- [ ] **Step 2: Add paramiko indirection**

After `_ldap_escape`, add:

```python
def _paramiko_client():
    """Lazy import; returns a fresh paramiko.SSHClient instance.
    Tests monkeypatch this to inject a mock client."""
    import paramiko
    return paramiko.SSHClient()


def _ssh_load_known_hosts(client, path: str) -> None:
    """Loads known_hosts; raises ResolutionFailed on missing/invalid."""
    try:
        client.load_host_keys(path)
    except (FileNotFoundError, OSError) as exc:
        raise ResolutionFailed("backend_error", f"known_hosts {path}: {exc}")
```

The reason for the `_paramiko_client()` factory: tests monkeypatch it with a `MagicMock()` factory and never touch real paramiko.

- [ ] **Step 3: Implement `_resolve_posix_ssh`**

Insert before `# ── Public dispatcher` block:

```python
import re
import shlex

_SAFE_UID = re.compile(r"^\d+$")


def _resolve_posix_ssh(source, binding) -> ResolveResult:
    cfg = source.connection_config or {}
    host = cfg.get("host")
    if not host:
        raise UnsupportedResolution(
            "Source missing host in connection_config"
        )
    port = int(cfg.get("port", 22))
    username = cfg.get("username")
    if not username:
        raise UnsupportedResolution(
            "Source missing username in connection_config"
        )
    password = cfg.get("password") or None
    key_path = cfg.get("key_path") or None
    key_passphrase = cfg.get("key_passphrase") or None
    known_hosts_path = cfg.get("known_hosts_path") or None

    identifier = binding.identifier
    if not _SAFE_UID.match(identifier):
        # Reject anything that isn't a bare integer uid — protects against
        # command injection via crafted identifiers.
        raise ResolutionFailed(
            "not_found",
            f"identifier {identifier!r} is not a numeric uid",
        )

    client = _paramiko_client()
    try:
        if known_hosts_path:
            _ssh_load_known_hosts(client, known_hosts_path)
        else:
            # Strict by default. If the deployer trusts the source enough
            # to skip known_hosts, they need to explicitly set it on the
            # source. We err on the side of refusing rather than auto-add.
            raise UnsupportedResolution(
                "Source missing known_hosts_path; refusing to auto-trust host key"
            )

        connect_kwargs = {
            "hostname": host,
            "port": port,
            "username": username,
            "timeout": 10,
            "auth_timeout": 10,
            "banner_timeout": 10,
        }
        if key_path:
            connect_kwargs["key_filename"] = key_path
            if key_passphrase:
                connect_kwargs["passphrase"] = key_passphrase
        if password:
            connect_kwargs["password"] = password

        try:
            client.connect(**connect_kwargs)
        except Exception as exc:  # noqa: BLE001
            raise ResolutionFailed("backend_error", f"ssh connect: {exc}")

        # `id -Gn <uid>` returns space-separated group names. We use names
        # (not gids) to align with Phase 12 ACL token vocabulary which
        # carries names where available.
        cmd = f"id -Gn {shlex.quote(identifier)}"
        try:
            stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
            rc = stdout.channel.recv_exit_status()
            out = stdout.read().decode("utf-8", errors="replace").strip()
            err = stderr.read().decode("utf-8", errors="replace").strip()
        except Exception as exc:  # noqa: BLE001
            raise ResolutionFailed("backend_error", f"ssh exec: {exc}")

        if rc != 0:
            # `id` returns 1 with "id: <id>: no such user" when the uid
            # doesn't resolve.
            if "no such user" in err.lower():
                raise ResolutionFailed("not_found", err or f"uid {identifier} not found")
            raise ResolutionFailed("backend_error", err or f"id exited {rc}")

        groups = [g for g in out.split() if g]
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass

    return ResolveResult(
        groups=groups,
        source="ssh",
        resolved_at=datetime.now(timezone.utc),
    )
```

- [ ] **Step 4: Wire dispatcher**

In `resolve_groups`, replace:
```python
        if src_type == "ssh":
            raise UnsupportedResolution(
                "SSH POSIX group resolution is not yet implemented (Phase 14b)"
            )
```

With:
```python
        if src_type == "ssh":
            return _resolve_posix_ssh(source, binding)
```

- [ ] **Step 5: Update module docstring**

Replace:
```
Phase 14b will add SSH POSIX (subprocess) and NT SMB (SAMR over DCE/RPC).
```

With:
```
Phase 14c will add NT SMB (SAMR over DCE/RPC).
```

---

## Task 3 — Tests

**Files:**
- Create: `api/tests/test_group_resolver_ssh.py`

- [ ] **Step 1: Test scaffold + happy path**

```python
"""Phase 14b tests — SSH POSIX group resolution path."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from akashic.services import group_resolver
from akashic.services.group_resolver import (
    ResolutionFailed,
    UnsupportedResolution,
    resolve_groups,
)


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


@pytest.mark.asyncio
async def test_ssh_happy_path(monkeypatch):
    client = _mock_client()
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: client)
    monkeypatch.setattr(
        group_resolver, "_ssh_load_known_hosts", lambda c, p: None
    )

    src = _src(host="host", port=22, username="u", known_hosts_path="/k")
    res = await resolve_groups(src, _binding())

    assert res.source == "ssh"
    assert res.groups == ["users", "wheel", "docker"]
    client.connect.assert_called_once()
    client.exec_command.assert_called_once()
    assert "id -Gn 1000" in client.exec_command.call_args.args[0]
```

- [ ] **Step 2: Failure modes**

Add:

```python
@pytest.mark.asyncio
async def test_ssh_missing_host(monkeypatch):
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: MagicMock())
    src = _src(username="u", known_hosts_path="/k")  # no host
    with pytest.raises(UnsupportedResolution):
        await resolve_groups(src, _binding())


@pytest.mark.asyncio
async def test_ssh_missing_known_hosts(monkeypatch):
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: MagicMock())
    src = _src(host="h", username="u")  # no known_hosts_path
    with pytest.raises(UnsupportedResolution):
        await resolve_groups(src, _binding())


@pytest.mark.asyncio
async def test_ssh_non_numeric_identifier(monkeypatch):
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: MagicMock())
    src = _src(host="h", username="u", known_hosts_path="/k")
    with pytest.raises(ResolutionFailed) as ei:
        await resolve_groups(src, _binding(identifier="; rm -rf /"))
    assert ei.value.reason == "not_found"


@pytest.mark.asyncio
async def test_ssh_connect_failure(monkeypatch):
    client = MagicMock()
    client.connect.side_effect = OSError("conn refused")
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: client)
    monkeypatch.setattr(group_resolver, "_ssh_load_known_hosts", lambda c, p: None)
    src = _src(host="h", username="u", known_hosts_path="/k")
    with pytest.raises(ResolutionFailed) as ei:
        await resolve_groups(src, _binding())
    assert ei.value.reason == "backend_error"


@pytest.mark.asyncio
async def test_ssh_id_no_such_user(monkeypatch):
    client = _mock_client(stdout="", stderr="id: '999999': no such user", rc=1)
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: client)
    monkeypatch.setattr(group_resolver, "_ssh_load_known_hosts", lambda c, p: None)
    src = _src(host="h", username="u", known_hosts_path="/k")
    with pytest.raises(ResolutionFailed) as ei:
        await resolve_groups(src, _binding(identifier="999999"))
    assert ei.value.reason == "not_found"


@pytest.mark.asyncio
async def test_ssh_id_other_failure(monkeypatch):
    client = _mock_client(stdout="", stderr="permission denied", rc=2)
    monkeypatch.setattr(group_resolver, "_paramiko_client", lambda: client)
    monkeypatch.setattr(group_resolver, "_ssh_load_known_hosts", lambda c, p: None)
    src = _src(host="h", username="u", known_hosts_path="/k")
    with pytest.raises(ResolutionFailed) as ei:
        await resolve_groups(src, _binding())
    assert ei.value.reason == "backend_error"
```

- [ ] **Step 3: Run tests**

```
docker compose -f docker-compose.eff5.yml run --rm api pytest tests/test_group_resolver_ssh.py -v
```

Expected: 6 passed.

---

## Task 4 — Endpoint integration verification

The existing endpoint (`api/akashic/routers/group_resolution.py`) doesn't change. But verify it now dispatches SSH happily.

- [ ] **Step 1: Smoke test through the endpoint**

```
# In the eff5 stack, login → create ssh source → create identity+binding → resolve
docker compose -f docker-compose.eff5.yml exec api python -c "
import asyncio, json
from types import SimpleNamespace
from akashic.services.group_resolver import resolve_groups
# Just exercise the dispatcher with a fake source and verify the right helper
# is selected. (The full e2e against a real ssh host is out of scope.)
src = SimpleNamespace(type='ssh', connection_config={'host':'x','username':'u'})
bind = SimpleNamespace(identifier='1000', identity_type='posix_uid')
try:
    asyncio.run(resolve_groups(src, bind))
except Exception as e:
    print(type(e).__name__, str(e))
"
```

Expected: `UnsupportedResolution` (because no `known_hosts_path` set). Confirms the dispatch path is reaching `_resolve_posix_ssh`.

---

## Verification checklist

1. `docker compose -f docker-compose.eff5.yml build api` — paramiko installs cleanly.
2. `docker compose -f docker-compose.eff5.yml run --rm api python -c "import paramiko"` — succeeds.
3. `docker compose -f docker-compose.eff5.yml run --rm api pytest tests/test_group_resolver_ssh.py -v` — 6 tests pass.
4. `docker compose -f docker-compose.eff5.yml run --rm api pytest tests/test_group_resolver.py -v` — Phase 14a tests still pass (no regression).
5. Manual dispatcher trace (Task 4) — confirms ssh routing reaches `_resolve_posix_ssh` and reports the new `UnsupportedResolution` for missing `known_hosts_path` (not the Phase 14a "not yet implemented" message).
6. The audit `groups_auto_resolved` payload's `source` field accepts `"ssh"` — implicit because it's an opaque JSONB field.

---

## Out of scope (deferred to Phase 14c)

- NT SMB SAMR group resolution. Requires either:
  - **Python option:** add `impacket` dependency and call `samrconnect`/`samrlookupnames` directly.
  - **Go option:** new `scanner/internal/samr/` package + scanner CLI subcommand for the API to invoke.
- Full e2e SSH test against a live SSH source. The unit tests cover behavior; live integration is a manual smoke step the deployer runs once.
- Connection-pooling for repeated SSH calls (each binding resolution opens its own connection — acceptable since it's user-initiated and rare).
