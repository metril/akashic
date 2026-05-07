"""Group-membership auto-resolution for FsBindings.

Per Phase 14a + 14b + 14c scope:
  - source.type=local|nfs + posix_uid → Python pwd/grp stdlib (NSS)
  - source.type=smb      + sid       → akashic-scanner subprocess (SAMR over DCE/RPC)
  - identity_type=nfsv4_principal     → LDAP (memberOf attribute)
  - everything else                   → UnsupportedResolution
"""
from __future__ import annotations

import json
import logging
import os
import pwd
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ── Shapes / errors ─────────────────────────────────────────────────────────

class ResolveResult(BaseModel):
    groups: list[str]
    source: Literal["nss", "ldap", "samr"]
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


# ── Per-implementation helpers ──────────────────────────────────────────────


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
    from akashic.services.source_config import merge_host_and_source
    cfg = merge_host_and_source(getattr(source, "host", None), source)
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


_SCANNER_BIN_ENV = "AKASHIC_SCANNER_BIN"


def _scanner_binary_path() -> str | None:
    """Returns the akashic-scanner binary path, or None if not findable.
    Tests can monkeypatch this to inject a fake."""
    explicit = os.environ.get(_SCANNER_BIN_ENV)
    if explicit and os.path.isfile(explicit):
        return explicit
    return shutil.which("akashic-scanner")


def _run_scanner(argv: list[str], password: str = "", timeout: int = 30) -> subprocess.CompletedProcess:
    """Indirection for tests to monkeypatch.

    The password is sent on stdin as a single JSON line so it doesn't show up
    in /proc/<pid>/cmdline. stdin is otherwise DEVNULL-equivalent (we only
    write the password line and immediately close)."""
    payload = json.dumps({"password": password}) + "\n"
    return subprocess.run(
        argv, capture_output=True, timeout=timeout, text=True,
        input=payload,
    )


def _resolve_smb_samr(source, binding) -> ResolveResult:
    """Resolve groups for an NT SID against an SMB source by spawning
    `akashic-scanner resolve-groups`. The Go process opens a DCE/RPC
    connection over the SMB IPC$ \\PIPE\\samr endpoint, runs the SAMR
    sequence, and writes a JSON {groups, source} object to stdout."""
    from akashic.services.source_config import merge_host_and_source
    cfg = merge_host_and_source(getattr(source, "host", None), source)
    host = cfg.get("host")
    if not host:
        raise UnsupportedResolution("Source missing host in connection_config")
    username = cfg.get("username")
    if not username:
        raise UnsupportedResolution("Source missing username in connection_config")
    port = int(cfg.get("port") or 445)
    password = cfg.get("password") or ""

    sid = (binding.identifier or "").strip()
    if not sid.upper().startswith("S-1-"):
        raise ResolutionFailed("not_found", f"identifier {sid!r} is not a SID")

    binary = _scanner_binary_path()
    if not binary:
        raise UnsupportedResolution(
            "akashic-scanner binary not found on PATH; "
            f"set {_SCANNER_BIN_ENV} or install the scanner binary"
        )

    argv = [
        binary, "resolve-groups",
        "--type=smb",
        "--host", host,
        "--port", str(port),
        "--user", username,
        "--password-stdin",
        "--sid", sid,
    ]
    try:
        proc = _run_scanner(argv, password=password)
    except subprocess.TimeoutExpired:
        raise ResolutionFailed("backend_error", "scanner timeout")
    except OSError as exc:
        raise ResolutionFailed("backend_error", f"scanner spawn: {exc}")

    if proc.returncode == 2:
        raise ResolutionFailed(
            "not_found",
            (proc.stderr or "user not found in domain").strip(),
        )
    if proc.returncode != 0:
        raise ResolutionFailed(
            "backend_error",
            (proc.stderr or f"scanner exited {proc.returncode}").strip(),
        )

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ResolutionFailed("backend_error", f"scanner output not JSON: {exc}")

    return ResolveResult(
        groups=payload.get("groups", []) or [],
        source="samr",
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
        raise UnsupportedResolution(
            f"posix_uid resolution not supported on source.type={src_type!r}"
        )

    if id_type == "sid":
        if src_type == "smb":
            return _resolve_smb_samr(source, binding)
        raise UnsupportedResolution(
            f"sid resolution not supported on source.type={src_type!r}"
        )

    if id_type == "s3_canonical":
        raise UnsupportedResolution("S3 has no group concept")

    raise UnsupportedResolution(f"Unknown identity_type: {id_type!r}")
