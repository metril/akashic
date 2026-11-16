"""Test doubles for the directory readers."""
from __future__ import annotations

from ldap.controls import SimplePagedResultsControl


class FakeLdapConn:
    """Minimal python-ldap connection.

    ``users`` / ``groups`` are lists of pages; each page is a list of
    ``(dn, attrs)``. ``token_groups`` maps dn -> list of raw SID bytes, or an
    Exception to raise. ``person_results`` answers ``search_s`` person lookups.
    """

    def __init__(self, users=(), groups=(), token_groups=None,
                 bind_error=None, person_results=()):
        self.users = list(users)
        self.groups = list(groups)
        self.token_groups = token_groups or {}
        self.bind_error = bind_error
        self.person_results = (
            person_results if isinstance(person_results, Exception) else list(person_results))
        self.options: dict = {}
        self.searches: list[tuple[str, str]] = []  # (base, filter)
        self.bound_as: tuple | None = None
        self.unbound = False
        self._pending: dict[int, tuple[list, bytes]] = {}

    def set_option(self, key, value):
        self.options[key] = value

    def simple_bind_s(self, dn, password):
        self.bound_as = (dn, password)
        if self.bind_error is not None:
            raise self.bind_error

    def unbind_s(self):
        self.unbound = True

    def search_ext(self, base, scope, flt, attrs, serverctrls=()):
        self.searches.append((base, flt))
        pages = self.groups if "group" in flt else self.users
        cookie = serverctrls[0].cookie
        msgid = len(self._pending) + 1
        self._pending[msgid] = (pages, cookie)
        return msgid

    def result3(self, msgid):
        pages, cookie = self._pending.pop(msgid)
        idx = int(cookie) if cookie else 0
        nxt = str(idx + 1).encode() if idx + 1 < len(pages) else b""
        page = pages[idx] if pages else []
        ctrl = SimplePagedResultsControl(True, size=500, cookie=nxt)
        return 101, page, msgid, [ctrl]

    def search_s(self, base, scope, flt, attrs=None):
        self.searches.append((base, flt))
        if attrs == ["tokenGroups"]:
            tg = self.token_groups.get(base)
            if isinstance(tg, Exception):
                raise tg
            return [(base, {"tokenGroups": tg or []})]
        if isinstance(self.person_results, Exception):
            raise self.person_results
        return self.person_results
