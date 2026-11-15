"""Typed registry of runtime-tunable settings + in-memory snapshot.

Usage (sync, anywhere):  ``from akashic.services.runtime_settings import rt``
then ``rt().cookie_secure``. The snapshot is loaded from the
``server_settings`` table at startup and reloaded on every
``setting.changed`` pubsub event. Until loaded it returns registry defaults.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

logger = logging.getLogger(__name__)

MASK = "••••"


@dataclass(frozen=True)
class Spec:
    key: str
    annotation: Any
    default: Any
    group: str
    description: str
    secret: bool = False
    restart: bool = False
    legacy_env: str | None = None  # defaults to key.upper()
    type_name: str = ""
    choices: tuple | None = None

    @property
    def env_name(self) -> str:
        return self.legacy_env or self.key.upper()

    def adapter(self) -> TypeAdapter:
        return TypeAdapter(self.annotation)

    def validate(self, value: Any) -> Any:
        return self.adapter().validate_python(value, strict=False)


def _s(key, ann, default, group, desc, tname, **kw) -> Spec:
    choices = None
    if getattr(ann, "__origin__", None) is Literal:
        choices = tuple(ann.__args__)
        tname = "enum"
    return Spec(key, ann, default, group, desc, type_name=tname, choices=choices, **kw)


_S = [
    # Auth / sessions
    _s("access_token_expire_minutes", int, 60, "auth", "Access token lifetime (minutes).", "int"),
    _s("refresh_token_expire_days", int, 30, "auth", "Refresh token / session lifetime (days).", "int"),
    _s("cookie_secure", bool, True, "auth", "Set the Secure flag on auth cookies (disable only for plain-HTTP dev).", "bool"),
    _s("cors_allow_origins", list[str], [], "auth", "Origins allowed for cross-origin browser requests (empty = same-origin only).", "list[str]"),
    _s("frontend_url", str, "", "auth", "Base URL of the SPA for post-login redirects (empty = same origin).", "str"),
    # OIDC
    _s("oidc_enabled", bool, False, "oidc", "Enable OIDC single sign-on.", "bool"),
    _s("oidc_discovery_url", str, "", "oidc", "OIDC discovery document URL.", "str"),
    _s("oidc_client_id", str, "", "oidc", "OIDC client ID.", "str"),
    _s("oidc_client_secret", str, "", "oidc", "OIDC client secret.", "str", secret=True),
    _s("oidc_redirect_uri", str, "http://localhost:8000/api/v1/auth/oidc/callback", "oidc", "OIDC redirect URI.", "str"),
    _s("oidc_strategy", Literal["auto", "claim", "ldap_fallback", "smb_lookup", "name_match"], "auto", "oidc", "Group-to-binding resolution strategy.", "enum"),
    _s("oidc_username_claim", str, "preferred_username", "oidc", "Username claim.", "str"),
    _s("oidc_email_claim", str, "email", "oidc", "Email claim.", "str"),
    _s("oidc_sid_claim", str, "onprem_sid", "oidc", "AD SID claim.", "str"),
    _s("oidc_uid_claim", str, "uidNumber", "oidc", "POSIX UID claim.", "str"),
    _s("oidc_groups_claim", str, "groups", "oidc", "Groups claim.", "str"),
    _s("oidc_groups_format", Literal["sid", "name", "dn"], "sid", "oidc", "Format of values in the groups claim.", "enum"),
    _s("oidc_dn_claim", str, "ldap_dn", "oidc", "LDAP DN claim (used by ldap_fallback).", "str"),
    # LDAP
    _s("ldap_enabled", bool, False, "ldap", "Enable LDAP login.", "bool"),
    _s("ldap_server", str, "", "ldap", "LDAP server URI, e.g. ldap://host:389.", "str"),
    _s("ldap_bind_dn", str, "", "ldap", "Bind DN.", "str"),
    _s("ldap_bind_password", str, "", "ldap", "Bind password.", "str", secret=True),
    _s("ldap_user_base", str, "", "ldap", "User search base DN.", "str"),
    _s("ldap_user_filter", str, "(uid={username})", "ldap", "User search filter ({username} placeholder).", "str"),
    # Search & content
    _s("tika_url", str, "http://tika:9998", "search", "Apache Tika server URL.", "str"),
    _s("fetch_concurrency", int, 10, "search", "Max simultaneous content-fetch subprocesses per API process.", "int", legacy_env="AKASHIC_FETCH_CONCURRENCY"),
    # Maintenance
    _s("stale_scan_threshold_minutes", int, 60, "maintenance", "Recover scans stuck running after this many minutes.", "int"),
    _s("audit_retention_days", int, 0, "maintenance", "Delete audit events older than this (0 = keep forever).", "int"),
    _s("group_cache_ttl_hours", int, 24, "maintenance", "Group-resolution cache TTL (hours).", "int"),
    # Performance
    _s("slow_query_ms", int, 100, "performance", "Log SQL statements slower than this (ms).", "int"),
    _s("slow_query_ms_overrides", dict[str, int], {}, "performance", "Per-endpoint-prefix slow-query thresholds (ms).", "dict[str,int]"),
    _s("streaming_topchildren", bool, False, "performance", "Incrementally recompute top_children during scans.", "bool", restart=True),
    # Feature flags
    _s("browse_enforce_perms", bool, False, "features", "Apply per-user ACL filtering in Browse and entry views.", "bool"),
    _s("discovery_enabled", bool, False, "features", "Allow scanners to be discovered and claimed.", "bool", legacy_env="SCANNER_DISCOVERY_ENABLED"),
]

REGISTRY: dict[str, Spec] = {s.key: s for s in _S}
GROUPS: list[tuple[str, str]] = [
    ("auth", "Auth & sessions"), ("oidc", "OIDC"), ("ldap", "LDAP"),
    ("search", "Search & content"), ("maintenance", "Maintenance"),
    ("performance", "Performance"), ("features", "Feature flags"),
]

_values: dict[str, Any] = {}


class _Snapshot:
    def __getattr__(self, name: str) -> Any:
        if name in _values:
            return _values[name]
        spec = REGISTRY.get(name)
        if spec is None:
            raise AttributeError(name)
        d = spec.default
        return type(d)(d) if isinstance(d, (list, dict)) else d


_snap = _Snapshot()


def rt() -> _Snapshot:
    """Current runtime settings snapshot (sync; defaults until loaded)."""
    return _snap


def coerce(spec: Spec, value: Any) -> Any:
    """Validate/coerce an API or env value to the spec type; raises ValueError."""
    try:
        return spec.validate(value)
    except ValidationError as e:
        raise ValueError(f"{spec.key}: {e.errors()[0]['msg']}") from e


def encode_stored(spec: Spec, value: Any) -> Any:
    """Value as persisted in the DB (secrets Fernet-encrypted)."""
    if spec.secret and value:
        from akashic.services.secret_encryption import encrypt_secret
        return encrypt_secret(value)
    return value


def decode_stored(spec: Spec, stored: Any) -> Any:
    if spec.secret:
        if not stored:
            return ""
        from akashic.services.secret_encryption import decrypt_secret
        try:
            return decrypt_secret(stored)
        except Exception:  # noqa: BLE001
            logger.warning("could not decrypt setting %s; treating as unset", spec.key)
            return ""
    return coerce(spec, stored)


async def load_snapshot(db) -> None:
    """Reload the in-memory snapshot from the DB."""
    from sqlalchemy import select
    from akashic.models.server_setting import ServerSetting

    rows = (await db.execute(select(ServerSetting))).scalars().all()
    new: dict[str, Any] = {}
    for r in rows:
        spec = REGISTRY.get(r.key)
        if spec is None:
            continue
        try:
            new[r.key] = decode_stored(spec, r.value)
        except ValueError:
            logger.warning("invalid stored value for %s; using default", r.key)
    changed_oidc = any(
        _values.get(k) != new.get(k)
        for k in set(_values) | set(new)
        if k.startswith("oidc_")
    )
    _values.clear()
    _values.update(new)
    if changed_oidc:
        # Discovery/JWKS caches were fetched with the old settings.
        from akashic.auth.oidc import invalidate_cache
        invalidate_cache()


async def reload_snapshot() -> None:
    from akashic.database import async_session
    try:
        async with async_session() as db:
            await load_snapshot(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("runtime settings reload failed: %s", exc)


def legacy_env_value(spec: Spec) -> str | None:
    for k, v in os.environ.items():
        if k.upper() == spec.env_name and v != "":
            return v
    return None
