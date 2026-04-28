"""Group-membership auto-resolution for FsBindings.

Per the Phase 14a scope:
  - source.type=local|nfs + posix_uid → Python pwd/grp stdlib (NSS)
  - identity_type=nfsv4_principal      → LDAP (memberOf attribute)
  - everything else                    → UnsupportedResolution

Phase 14b will add SSH POSIX (subprocess) and NT SMB (SAMR over DCE/RPC).
"""
from __future__ import annotations

import logging
import os
import pwd
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ── Shapes / errors ─────────────────────────────────────────────────────────

class ResolveResult(BaseModel):
    groups: list[str]
    source: Literal["nss", "ldap"]
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
        results = conn.search_s(
            search_base,
            2,  # ldap.SCOPE_SUBTREE
            filterstr=f"(uid={local})",
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
            raise UnsupportedResolution(
                "SSH POSIX group resolution is not yet implemented (Phase 14b)"
            )
        raise UnsupportedResolution(
            f"posix_uid resolution not supported on source.type={src_type!r}"
        )

    if id_type == "sid":
        raise UnsupportedResolution(
            "NT/SID group resolution requires SAMR (Phase 14b)"
        )

    if id_type == "s3_canonical":
        raise UnsupportedResolution("S3 has no group concept")

    raise UnsupportedResolution(f"Unknown identity_type: {id_type!r}")
