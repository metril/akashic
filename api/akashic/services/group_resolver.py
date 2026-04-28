"""Group-membership auto-resolution for FsBindings.

Per Phase 14a + 14b scope:
  - source.type=local|nfs + posix_uid → Python pwd/grp stdlib (NSS)
  - source.type=ssh      + posix_uid → paramiko + `id -Gn` over SSH
  - identity_type=nfsv4_principal     → LDAP (memberOf attribute)
  - everything else                   → UnsupportedResolution

Phase 14c will add NT SMB (SAMR over DCE/RPC).
"""
from __future__ import annotations

import logging
import os
import pwd
import re
import shlex
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ── Shapes / errors ─────────────────────────────────────────────────────────

class ResolveResult(BaseModel):
    groups: list[str]
    source: Literal["nss", "ldap", "ssh"]
    resolved_at: datetime


class ResolutionFailed(Exception):
    """The resolver attempted resolution but the principal could not be
    authoritatively resolved (not_found, backend_error, etc.)."""
    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason


class UnsupportedResolution(Exception):
    """This (source.type, binding.identity_type) combination has no resolver."""
    pass


# ── Stdlib indirection (so tests can monkeypatch) ───────────────────────────

def _pwd_getpwuid(uid: int):
    return pwd.getpwuid(uid)


def _os_getgrouplist(name: str, base_gid: int):
    return os.getgrouplist(name, base_gid)


def _ldap_initialize(url: str):
    """Imported lazily because python-ldap doesn't ship on every dev box."""
    import ldap  # noqa
    return ldap.initialize(url)


def _ldap_escape(value: str) -> str:
    """Escape user-controlled values before interpolating into LDAP filters."""
    import ldap.filter
    return ldap.filter.escape_filter_chars(value)


def _paramiko_client():
    """Lazy import; returns a fresh paramiko.SSHClient instance.
    Tests monkeypatch this to inject a MagicMock and never touch real paramiko."""
    import paramiko
    return paramiko.SSHClient()


def _ssh_load_known_hosts(client, path: str) -> None:
    """Load a known_hosts file onto the client. Raises ResolutionFailed
    on missing/invalid file so the caller surfaces a structured error."""
    try:
        client.load_host_keys(path)
    except (FileNotFoundError, OSError) as exc:
        raise ResolutionFailed("backend_error", f"known_hosts {path}: {exc}")


# ── Per-implementation helpers ──────────────────────────────────────────────


_SAFE_UID = re.compile(r"^\d+$")


def _resolve_posix_local(identifier: str) -> ResolveResult:
    try:
        uid = int(identifier)
    except ValueError as exc:
        raise ResolutionFailed("not_found", f"identifier {identifier!r} is not a uid")

    try:
        pw = _pwd_getpwuid(uid)
    except KeyError:
        raise ResolutionFailed("not_found", f"uid {uid} not in passwd")

    try:
        gids = _os_getgrouplist(pw.pw_name, pw.pw_gid if hasattr(pw, "pw_gid") else 0)
    except Exception as exc:  # noqa: BLE001
        raise ResolutionFailed("backend_error", str(exc))

    return ResolveResult(
        groups=[str(g) for g in gids],
        source="nss",
        resolved_at=datetime.now(timezone.utc),
    )


def _resolve_ldap(source, binding) -> ResolveResult:
    cfg = source.connection_config or {}
    url        = cfg.get("ldap_url")
    bind_dn    = cfg.get("ldap_bind_dn", "")
    bind_pw    = cfg.get("ldap_bind_password", "")
    search_base = cfg.get("ldap_user_search_base")
    group_attr = cfg.get("ldap_group_attr", "memberOf")

    if not url or not search_base:
        raise UnsupportedResolution(
            "Source missing ldap_url or ldap_user_search_base in connection_config"
        )

    try:
        conn = _ldap_initialize(url)
        conn.simple_bind_s(bind_dn, bind_pw)
        # Filter by uid attribute against the principal's local-part.
        local = binding.identifier.split("@", 1)[0]
        filterstr = f"(uid={_ldap_escape(local)})"
        results = conn.search_s(
            search_base,
            2,  # ldap.SCOPE_SUBTREE
            filterstr=filterstr,
            attrlist=[group_attr],
        )
        try:
            conn.unbind_s()
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        raise ResolutionFailed("backend_error", str(exc))

    if not results:
        raise ResolutionFailed("not_found", f"no LDAP entry for uid={local!r}")

    _dn, attrs = results[0]
    raw_dns = attrs.get(group_attr, []) or []
    groups: list[str] = []
    for raw in raw_dns:
        s = raw.decode() if isinstance(raw, bytes) else raw
        # cn=engineers,ou=groups,dc=… → engineers
        cn = s.split(",", 1)[0]
        if cn.lower().startswith("cn="):
            groups.append(cn[3:])
        else:
            groups.append(s)

    return ResolveResult(
        groups=groups,
        source="ldap",
        resolved_at=datetime.now(timezone.utc),
    )


def _resolve_posix_ssh(source, binding) -> ResolveResult:
    """Resolve POSIX groups for a uid against an SSH source by running
    `id -Gn <uid>` remotely and parsing the space-separated group names.

    Connection details come from `source.connection_config` using the same
    keys the SSH connector uses: host, port, username, password, key_path,
    key_passphrase, known_hosts_path.

    Strict-host-key by default: if known_hosts_path is missing, refuses to
    auto-trust the remote host."""
    cfg = source.connection_config or {}
    host = cfg.get("host")
    if not host:
        raise UnsupportedResolution("Source missing host in connection_config")
    port = int(cfg.get("port") or 22)
    username = cfg.get("username")
    if not username:
        raise UnsupportedResolution("Source missing username in connection_config")
    password = cfg.get("password") or None
    key_path = cfg.get("key_path") or None
    key_passphrase = cfg.get("key_passphrase") or None
    known_hosts_path = cfg.get("known_hosts_path") or None

    identifier = binding.identifier
    if not _SAFE_UID.match(identifier or ""):
        # Bare numeric uid only — anything else is rejected to prevent
        # command-injection via crafted identifiers.
        raise ResolutionFailed(
            "not_found",
            f"identifier {identifier!r} is not a numeric uid",
        )

    if not known_hosts_path:
        # Strict by default — the deployer must explicitly opt into
        # host-key trust by setting known_hosts_path on the source.
        raise UnsupportedResolution(
            "Source missing known_hosts_path; refusing to auto-trust host key"
        )

    client = _paramiko_client()
    try:
        _ssh_load_known_hosts(client, known_hosts_path)

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

        cmd = f"id -Gn {shlex.quote(identifier)}"
        try:
            _stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
            rc = stdout.channel.recv_exit_status()
            out = stdout.read().decode("utf-8", errors="replace").strip()
            err = stderr.read().decode("utf-8", errors="replace").strip()
        except Exception as exc:  # noqa: BLE001
            raise ResolutionFailed("backend_error", f"ssh exec: {exc}")

        if rc != 0:
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


# ── Public dispatcher ───────────────────────────────────────────────────────


async def resolve_groups(source, binding) -> ResolveResult:
    """Resolve groups for a binding against its source. Raises:
       - UnsupportedResolution: combo isn't implemented (caller renders 422 hint)
       - ResolutionFailed: backend reachable but principal not findable
    """
    src_type = getattr(source, "type", None)
    id_type = getattr(binding, "identity_type", None)

    # NFSv4 always tries LDAP if available, regardless of source.type.
    if id_type == "nfsv4_principal":
        return _resolve_ldap(source, binding)

    if id_type == "posix_uid":
        if src_type in ("local", "nfs"):
            return _resolve_posix_local(binding.identifier)
        if src_type == "ssh":
            return _resolve_posix_ssh(source, binding)
        raise UnsupportedResolution(
            f"posix_uid resolution not supported on source.type={src_type!r}"
        )

    if id_type == "sid":
        raise UnsupportedResolution(
            "NT/SID group resolution requires SAMR (Phase 14c)"
        )

    if id_type == "s3_canonical":
        raise UnsupportedResolution("S3 has no group concept")

    raise UnsupportedResolution(f"Unknown identity_type: {id_type!r}")
