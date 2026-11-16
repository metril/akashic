"""Active Directory reader: users, groups and tokenGroups via python-ldap.

Synchronous; callers use ``asyncio.to_thread``. Reuses the ``ldap_*`` runtime
settings of the LDAP login. Error messages carry only the server's own
``desc``/``info`` fields, never the bind password.
"""
from __future__ import annotations

import logging
import threading

import ldap  # python-ldap
from ldap.controls import SimplePagedResultsControl
from ldap.filter import escape_filter_chars

from akashic.services.directory.records import DirectoryError, DirRecord, NotConfigured
from akashic.services.runtime_settings import rt
from akashic.services.sid import decode_binary_sid

logger = logging.getLogger(__name__)

# Connection-level failures abort the whole fetch; other LDAP errors on one
# object only mark that row's groups as failed.
_FATAL = (ldap.SERVER_DOWN, ldap.TIMEOUT, ldap.CONNECT_ERROR, ldap.UNAVAILABLE)
PAGE_SIZE = 500
USER_FILTER = "(&(objectCategory=person)(objectClass=user))"
GROUP_FILTER = "(objectClass=group)"
ATTRS = [
    "objectGUID", "objectSid", "distinguishedName", "sAMAccountName",
    "displayName", "cn", "mail", "uidNumber", "gidNumber",
]


def sid_to_str(raw: bytes) -> str:
    sid = decode_binary_sid(raw)
    if sid is None:
        raise ValueError("malformed binary SID")
    return sid


def ad_configured() -> bool:
    return bool(rt().ldap_server and rt().ldap_user_base)


def _ldap_initialize(url: str):
    return ldap.initialize(url)


def _redact(exc: ldap.LDAPError) -> str:
    info = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {}
    desc = info.get("desc") or type(exc).__name__
    extra = info.get("info")
    return f"AD: {type(exc).__name__}: {desc}" + (f" ({extra})" if extra else "")


def _connect(timeout: float):
    if not ad_configured():
        raise NotConfigured("AD is not configured")
    conn = _ldap_initialize(rt().ldap_server)
    try:
        conn.set_option(ldap.OPT_REFERRALS, 0)
        conn.set_option(ldap.OPT_NETWORK_TIMEOUT, timeout)
        conn.set_option(ldap.OPT_TIMEOUT, timeout)
        conn.simple_bind_s(rt().ldap_bind_dn, rt().ldap_bind_password)
    except ldap.LDAPError as exc:
        _unbind(conn)
        msg = _redact(exc)
        logger.warning("%s", msg)
        raise DirectoryError(msg) from None
    return conn


def _unbind(conn) -> None:
    try:
        conn.unbind_s()
    except Exception:  # noqa: BLE001
        pass


def _first(attrs: dict, key: str) -> str | None:
    vals = attrs.get(key)
    if not vals:
        return None
    v = vals[0]
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else v


def _int(attrs: dict, key: str) -> int | None:
    v = _first(attrs, key)
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def _token_groups(conn, dn: str, own_sid: str | None) -> tuple[str, ...]:
    res = conn.search_s(dn, ldap.SCOPE_BASE, "(objectClass=*)", ["tokenGroups"])
    sids: list[str] = []
    for rdn, attrs in res:
        if rdn is None:
            continue
        for raw in attrs.get("tokenGroups") or []:
            sid = decode_binary_sid(raw)
            if sid is None:
                raise DirectoryError("AD: malformed tokenGroups SID")
            if sid != own_sid and sid not in sids:
                sids.append(sid)
    return tuple(sids)


def _record(conn, dn: str, attrs: dict, kind: str) -> DirRecord | None:
    guid = attrs.get("objectGUID")
    if not guid:
        return None
    raw_sid = attrs.get("objectSid")
    sid = None
    if raw_sid:
        sid = decode_binary_sid(raw_sid[0])
        if sid is None:
            raise DirectoryError("AD: malformed objectSid")
    try:
        group_sids = _token_groups(conn, dn, sid)
        status = "ok"
    except _FATAL:
        raise
    except ldap.LDAPError as exc:
        logger.warning("%s (tokenGroups)", _redact(exc))
        group_sids, status = (), "failed"
    return DirRecord(
        backend="ad",
        ref=guid[0].hex(),
        kind=kind,
        display_name=_first(attrs, "displayName") or _first(attrs, "cn"),
        username=_first(attrs, "sAMAccountName"),
        email=_first(attrs, "mail"),
        sid=sid,
        dn=_first(attrs, "distinguishedName") or dn,
        group_sids=group_sids,
        groups_status=status,
        uid_number=_int(attrs, "uidNumber"),
        gid_number=_int(attrs, "gidNumber"),
    )


def _check_stop(stop: threading.Event | None) -> None:
    if stop is not None and stop.is_set():
        raise DirectoryError("AD: cancelled")


def _paged(conn, base: str, flt: str, stop: threading.Event | None = None):
    ctrl = SimplePagedResultsControl(True, size=PAGE_SIZE, cookie=b"")
    while True:
        _check_stop(stop)
        msgid = conn.search_ext(base, ldap.SCOPE_SUBTREE, flt, ATTRS, serverctrls=[ctrl])
        _rtype, rdata, _rmsgid, rctrls = conn.result3(msgid)
        for dn, attrs in rdata:
            if dn is not None:  # skip referrals
                yield dn, attrs
        paged = [
            c for c in rctrls or []
            if c.controlType == SimplePagedResultsControl.controlType
        ]
        if not paged or not paged[0].cookie:
            return
        ctrl.cookie = paged[0].cookie


def fetch_all_ad(
    timeout: float = 10.0, stop: threading.Event | None = None,
) -> list[DirRecord]:
    """All AD users and groups. ``stop`` (checked per page and object) aborts
    the fetch with ``DirectoryError`` so a cancelled task's thread ends."""
    _check_stop(stop)
    conn = _connect(timeout)
    try:
        group_base = rt().ldap_group_base or rt().ldap_user_base
        out: list[DirRecord] = []
        for base, flt, kind in (
            (rt().ldap_user_base, USER_FILTER, "person"),
            (group_base, GROUP_FILTER, "group"),
        ):
            for dn, attrs in _paged(conn, base, flt, stop):
                _check_stop(stop)
                rec = _record(conn, dn, attrs, kind)
                if rec is not None:
                    out.append(rec)
        return out
    except ldap.LDAPError as exc:
        msg = _redact(exc)
        logger.warning("%s", msg)
        raise DirectoryError(msg) from None
    finally:
        _unbind(conn)


def fetch_ad_person(
    *, sid: str | None, dn: str | None, timeout: float = 3.0,
) -> DirRecord | None:
    """One person by SID or DN. ``None`` means authoritatively not found."""
    if not sid and not dn:
        return None
    conn = _connect(timeout)
    try:
        if sid:
            flt = f"(&{USER_FILTER}(objectSid={escape_filter_chars(sid)}))"
            res = conn.search_s(rt().ldap_user_base, ldap.SCOPE_SUBTREE, flt, ATTRS)
        else:
            res = conn.search_s(dn, ldap.SCOPE_BASE, USER_FILTER, ATTRS)
        for rdn, attrs in res:
            if rdn is not None:
                return _record(conn, rdn, attrs, "person")
        return None
    except ldap.NO_SUCH_OBJECT:
        return None
    except ldap.LDAPError as exc:
        msg = _redact(exc)
        logger.warning("%s", msg)
        raise DirectoryError(msg) from None
    finally:
        _unbind(conn)
