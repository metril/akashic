"""Group resolver tests — mocks system calls / LDAP."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_posix_local_resolves_via_nss(monkeypatch):
    """For source.type=local + identity_type=posix_uid, resolve via pwd/grp."""
    from akashic.services.group_resolver import (
        ResolveResult, UnsupportedResolution, resolve_groups,
    )

    class _FakePwd:
        pw_name = "alice"
    monkeypatch.setattr("akashic.services.group_resolver._pwd_getpwuid", lambda uid: _FakePwd())
    monkeypatch.setattr("akashic.services.group_resolver._os_getgrouplist",
                         lambda name, base_gid: [100, 1000, 9999])

    class _FakeSource:
        type = "local"
        connection_config = {}
    class _FakeBinding:
        identity_type = "posix_uid"
        identifier = "1000"

    result = await resolve_groups(_FakeSource(), _FakeBinding())
    assert isinstance(result, ResolveResult)
    assert result.groups == ["100", "1000", "9999"]
    assert result.source == "nss"


@pytest.mark.asyncio
async def test_posix_unknown_uid_raises_not_found(monkeypatch):
    """When pwd.getpwuid raises KeyError, surface a clear error."""
    from akashic.services.group_resolver import (
        ResolutionFailed, resolve_groups,
    )

    def _raise(uid):
        raise KeyError("nope")
    monkeypatch.setattr("akashic.services.group_resolver._pwd_getpwuid", _raise)

    class _FakeSource:
        type = "local"
        connection_config = {}
    class _FakeBinding:
        identity_type = "posix_uid"
        identifier = "999999"

    with pytest.raises(ResolutionFailed) as exc:
        await resolve_groups(_FakeSource(), _FakeBinding())
    assert exc.value.reason == "not_found"


@pytest.mark.asyncio
async def test_ssh_empty_config_unsupported(monkeypatch):
    """SSH source with no connection_config should surface UnsupportedResolution
    (missing host). Phase 14b SSH-specific behavior is covered in
    test_group_resolver_ssh.py."""
    from akashic.services.group_resolver import UnsupportedResolution, resolve_groups

    class _FakeSource:
        type = "ssh"
        connection_config = {}
    class _FakeBinding:
        identity_type = "posix_uid"
        identifier = "1000"

    with pytest.raises(UnsupportedResolution):
        await resolve_groups(_FakeSource(), _FakeBinding())


@pytest.mark.asyncio
async def test_smb_unsupported(monkeypatch):
    """SMB sources defer to 14c (SAMR); resolver returns Unsupported."""
    from akashic.services.group_resolver import UnsupportedResolution, resolve_groups

    class _FakeSource:
        type = "smb"
        connection_config = {}
    class _FakeBinding:
        identity_type = "sid"
        identifier = "S-1-5-21-1-2-3-1013"

    with pytest.raises(UnsupportedResolution):
        await resolve_groups(_FakeSource(), _FakeBinding())


@pytest.mark.asyncio
async def test_s3_unsupported():
    from akashic.services.group_resolver import UnsupportedResolution, resolve_groups

    class _FakeSource:
        type = "s3"
        connection_config = {}
    class _FakeBinding:
        identity_type = "s3_canonical"
        identifier = "acct-1"

    with pytest.raises(UnsupportedResolution):
        await resolve_groups(_FakeSource(), _FakeBinding())


@pytest.mark.asyncio
async def test_ldap_resolves_memberof(monkeypatch):
    """For identity_type=nfsv4_principal, query LDAP for memberOf."""
    from akashic.services.group_resolver import ResolveResult, resolve_groups

    fake_ldap_results = [
        # python-ldap returns (dn, attrs) tuples
        ("uid=alice,ou=people,dc=example,dc=com", {
            "memberOf": [
                b"cn=engineers,ou=groups,dc=example,dc=com",
                b"cn=admins,ou=groups,dc=example,dc=com",
            ],
        }),
    ]

    class _FakeLdap:
        def simple_bind_s(self, *_a, **_k): pass
        def search_s(self, base, scope, filterstr=None, attrlist=None):
            return fake_ldap_results
        def unbind_s(self): pass

    monkeypatch.setattr("akashic.services.group_resolver._ldap_initialize",
                         lambda url: _FakeLdap())

    class _FakeSource:
        type = "nfs"
        connection_config = {
            "ldap_url": "ldap://ldap.example.com",
            "ldap_bind_dn": "cn=svc,dc=example,dc=com",
            "ldap_bind_password": "x",
            "ldap_user_search_base": "ou=people,dc=example,dc=com",
        }
    class _FakeBinding:
        identity_type = "nfsv4_principal"
        identifier = "alice@example.com"

    result = await resolve_groups(_FakeSource(), _FakeBinding())
    assert isinstance(result, ResolveResult)
    assert "engineers" in result.groups
    assert "admins" in result.groups
    assert result.source == "ldap"


@pytest.mark.asyncio
async def test_ldap_no_config_raises_unsupported():
    """LDAP-required type without LDAP config in source → Unsupported."""
    from akashic.services.group_resolver import UnsupportedResolution, resolve_groups

    class _FakeSource:
        type = "nfs"
        connection_config = {}  # no ldap_url
    class _FakeBinding:
        identity_type = "nfsv4_principal"
        identifier = "alice"

    with pytest.raises(UnsupportedResolution):
        await resolve_groups(_FakeSource(), _FakeBinding())


@pytest.mark.asyncio
async def test_ldap_backend_error_raises_resolution_failed(monkeypatch):
    """LDAP server unreachable surfaces as ResolutionFailed(backend_error)."""
    from akashic.services.group_resolver import ResolutionFailed, resolve_groups

    def _fail(_url):
        raise ConnectionRefusedError("ldap unreachable")
    monkeypatch.setattr("akashic.services.group_resolver._ldap_initialize", _fail)

    class _FakeSource:
        type = "nfs"
        connection_config = {
            "ldap_url": "ldap://nope",
            "ldap_bind_dn": "",
            "ldap_bind_password": "",
            "ldap_user_search_base": "ou=people,dc=x",
        }
    class _FakeBinding:
        identity_type = "nfsv4_principal"
        identifier = "alice"

    with pytest.raises(ResolutionFailed) as exc:
        await resolve_groups(_FakeSource(), _FakeBinding())
    assert exc.value.reason == "backend_error"


@pytest.mark.asyncio
async def test_ldap_filter_chars_are_escaped(monkeypatch):
    """A binding identifier with LDAP metachars must be escaped before
    being used in the search filter."""
    from akashic.services.group_resolver import resolve_groups

    captured: dict[str, str] = {}

    class _FakeLdap:
        def simple_bind_s(self, *_a, **_k): pass
        def search_s(self, base, scope, filterstr=None, attrlist=None):
            captured["filterstr"] = filterstr
            return []  # not_found
        def unbind_s(self): pass

    monkeypatch.setattr("akashic.services.group_resolver._ldap_initialize",
                        lambda url: _FakeLdap())

    class _FakeSource:
        type = "nfs"
        connection_config = {
            "ldap_url": "ldap://x",
            "ldap_bind_dn": "",
            "ldap_bind_password": "",
            "ldap_user_search_base": "ou=people,dc=x",
        }
    class _FakeBinding:
        identity_type = "nfsv4_principal"
        identifier = "*)(uid=*"  # injection attempt

    from akashic.services.group_resolver import ResolutionFailed
    with pytest.raises(ResolutionFailed):
        await resolve_groups(_FakeSource(), _FakeBinding())

    # The captured filter must NOT contain the raw asterisk pattern.
    # python-ldap's escape_filter_chars escapes * → \2a, ( → \28, ) → \29.
    assert "\\2a" in captured["filterstr"].lower() or "\\\\2a" in captured["filterstr"].lower()
    assert "*)(uid=*" not in captured["filterstr"]
