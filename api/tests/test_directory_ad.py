import logging

import ldap
import pytest

from akashic.services.directory import ad
from akashic.services.directory.records import DirectoryError
from tests.directory_fakes import FakeLdapConn

PASSWORD = "s3cret-bind-pw"
DOM = "S-1-5-21-1-2-3"


def sid_bytes(s: str) -> bytes:
    parts = [int(p) for p in s.split("-")[1:]]
    rev, auth, subs = parts[0], parts[1], parts[2:]
    return (bytes([rev, len(subs)]) + auth.to_bytes(6, "big")
            + b"".join(x.to_bytes(4, "little") for x in subs))


def user(n: int, **extra):
    attrs = {
        "objectGUID": [bytes([n]) * 16],
        "objectSid": [sid_bytes(f"{DOM}-{1000 + n}")],
        "distinguishedName": [f"CN=u{n},DC=x".encode()],
        "sAMAccountName": [f"u{n}".encode()],
        "mail": [f"u{n}@x.test".encode()],
    }
    attrs.update(extra)
    return f"CN=u{n},DC=x", attrs


GROUP = ("CN=g,OU=G,DC=x", {
    "objectGUID": [b"\xaa" * 16],
    "objectSid": [sid_bytes(f"{DOM}-513")],
    "distinguishedName": [b"CN=g,OU=G,DC=x"],
    "sAMAccountName": [b"g"],
    "cn": [b"Group G"],
})
PARENT = sid_bytes(f"{DOM}-600")


@pytest.fixture
def cfg(rt_set):
    rt_set(ldap_server="ldap://dc", ldap_bind_dn="cn=svc,dc=x", ldap_bind_password=PASSWORD,
           ldap_user_base="OU=U,DC=x", ldap_group_base="OU=G,DC=x")


def install(monkeypatch, conn):
    monkeypatch.setattr(ad, "_ldap_initialize", lambda url: conn)
    return conn


def test_sid_to_str():
    assert ad.sid_to_str(bytes.fromhex("01020000000000052000000020020000")) == "S-1-5-32-544"
    assert ad.sid_to_str(sid_bytes("S-1-5-21-1-2-3-1001")) == "S-1-5-21-1-2-3-1001"


def test_fetch_all_ad_users_groups_and_token_groups(cfg, monkeypatch):
    u1 = user(1, displayName=[b"User One"], uidNumber=[b"1000"], gidNumber=[b"100"])
    u2 = user(2)
    conn = install(monkeypatch, FakeLdapConn(
        users=[[u1, u2]], groups=[[GROUP]],
        token_groups={
            u1[0]: [sid_bytes(f"{DOM}-513"), sid_bytes(f"{DOM}-1001")],
            u2[0]: [],
            GROUP[0]: [PARENT, sid_bytes(f"{DOM}-513")],
        },
    ))
    recs = {r.username: r for r in ad.fetch_all_ad()}
    r1 = recs["u1"]
    assert r1.group_sids == (f"{DOM}-513",)  # own SID excluded
    assert r1.groups_status == "ok"
    assert r1.ref == (b"\x01" * 16).hex()
    assert r1.display_name == "User One"
    assert (r1.uid_number, r1.gid_number) == (1000, 100)
    assert r1.kind == "person" and r1.sid == f"{DOM}-1001"
    g = recs["g"]
    assert g.kind == "group" and g.display_name == "Group G"
    assert g.group_sids == (f"{DOM}-600",) and g.groups_status == "ok"
    assert recs["u2"].display_name is None
    bases = {b for b, f in conn.searches if "tokenGroups" not in f and f.startswith("(")}
    assert {"OU=U,DC=x", "OU=G,DC=x"} <= bases
    assert conn.unbound


def test_group_base_falls_back_to_user_base(cfg, rt_set, monkeypatch):
    rt_set(ldap_group_base="")
    conn = install(monkeypatch, FakeLdapConn(users=[[]], groups=[[]]))
    ad.fetch_all_ad()
    assert {b for b, _ in conn.searches} == {"OU=U,DC=x"}


def test_fetch_all_ad_token_groups_failure_marks_row(cfg, monkeypatch):
    u1, u2 = user(1), user(2)
    install(monkeypatch, FakeLdapConn(
        users=[[u1, u2]], groups=[[]],
        token_groups={u1[0]: [PARENT], u2[0]: ldap.INSUFFICIENT_ACCESS({"desc": "denied"})},
    ))
    recs = {r.username: r for r in ad.fetch_all_ad()}
    assert recs["u2"].groups_status == "failed" and recs["u2"].group_sids == ()
    assert recs["u1"].groups_status == "ok" and recs["u1"].group_sids == (f"{DOM}-600",)


def test_fetch_all_ad_pages(cfg, monkeypatch):
    install(monkeypatch, FakeLdapConn(users=[[user(1)], [user(2)], [user(3)]], groups=[[GROUP]]))
    recs = ad.fetch_all_ad()
    assert sorted(r.username for r in recs) == ["g", "u1", "u2", "u3"]


def test_ad_bind_failure_redacts_password(cfg, monkeypatch, caplog):
    install(monkeypatch, FakeLdapConn(
        bind_error=ldap.INVALID_CREDENTIALS(
            {"desc": "Invalid credentials", "info": "80090308", "matched": PASSWORD}),
    ))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(DirectoryError) as ei:
            ad.fetch_all_ad()
    assert "Invalid credentials" in str(ei.value)
    assert PASSWORD not in str(ei.value) and PASSWORD not in caplog.text


def test_fetch_ad_person_not_found_returns_none(cfg, monkeypatch):
    install(monkeypatch, FakeLdapConn(person_results=[]))
    assert ad.fetch_ad_person(sid=f"{DOM}-1", dn=None) is None
    assert ad.fetch_ad_person(sid=None, dn="CN=x,DC=x") is None


def test_fetch_ad_person_no_such_object_returns_none(cfg, monkeypatch):
    install(monkeypatch, FakeLdapConn(person_results=ldap.NO_SUCH_OBJECT({"desc": "gone"})))
    assert ad.fetch_ad_person(sid=None, dn="CN=x,DC=x") is None


def test_fetch_all_ad_connection_loss_aborts(cfg, monkeypatch, caplog):
    u1, u2 = user(1), user(2)
    install(monkeypatch, FakeLdapConn(
        users=[[u1, u2]], groups=[[]],
        token_groups={u1[0]: ldap.SERVER_DOWN({"desc": "Can't contact LDAP server"})},
    ))
    with pytest.raises(DirectoryError, match="SERVER_DOWN"):
        ad.fetch_all_ad()


def test_fetch_all_ad_malformed_sid_is_directory_error(cfg, monkeypatch):
    bad = user(1, objectSid=[b"\x01\x05"])
    install(monkeypatch, FakeLdapConn(users=[[bad]], groups=[[]]))
    with pytest.raises(DirectoryError, match="malformed objectSid"):
        ad.fetch_all_ad()


def test_fetch_all_ad_malformed_token_group_is_directory_error(cfg, monkeypatch):
    u1 = user(1)
    install(monkeypatch, FakeLdapConn(users=[[u1]], groups=[[]], token_groups={u1[0]: [b"\x01"]}))
    with pytest.raises(DirectoryError, match="tokenGroups"):
        ad.fetch_all_ad()


def test_sid_to_str_rejects_malformed():
    for raw in (b"", b"\x01\x05", bytes.fromhex("0102000000000005") + b"\x00" * 7):
        with pytest.raises(ValueError):
            ad.sid_to_str(raw)


def test_fetch_ad_person_by_sid_escapes_filter(cfg, monkeypatch):
    conn = install(monkeypatch, FakeLdapConn(person_results=[]))
    ad.fetch_ad_person(sid="S-1-5-21-1)(cn=*", dn=None)
    flt = conn.searches[-1][1]
    assert "(objectSid=S-1-5-21-1\\29\\28cn=\\2a)" in flt


def test_fetch_ad_person_found(cfg, monkeypatch):
    u = user(1)
    install(monkeypatch, FakeLdapConn(person_results=[u], token_groups={u[0]: [PARENT]}))
    rec = ad.fetch_ad_person(sid=f"{DOM}-1001", dn=None)
    assert rec.username == "u1" and rec.group_sids == (f"{DOM}-600",)


def test_ad_configured(cfg, rt_set):
    assert ad.ad_configured()
    rt_set(ldap_user_base="")
    assert not ad.ad_configured()


def test_fetch_all_ad_stops_when_event_set(cfg, monkeypatch):
    import threading
    u1, u2 = user(1), user(2)
    conn = install(monkeypatch, FakeLdapConn(users=[[u1, u2]], groups=[[]]))
    stop = threading.Event()
    stop.set()
    with pytest.raises(DirectoryError, match="AD: cancelled"):
        ad.fetch_all_ad(stop=stop)
    assert conn.searches == [] or all("tokenGroups" not in str(s) for s in conn.searches)


def test_fetch_all_ad_stops_mid_fetch(cfg, monkeypatch):
    import threading
    u1, u2 = user(1), user(2)
    stop = threading.Event()
    conn = FakeLdapConn(users=[[u1, u2]], groups=[[]])
    real = conn.search_s

    def search_s(base, scope, flt, attrs=None):
        if attrs == ["tokenGroups"]:
            stop.set()  # cancelled while the first object is being read
        return real(base, scope, flt, attrs)
    conn.search_s = search_s
    install(monkeypatch, conn)
    with pytest.raises(DirectoryError, match="AD: cancelled"):
        ad.fetch_all_ad(stop=stop)
    assert sum(1 for _b, f in conn.searches if f == "(objectClass=*)") == 1
    assert conn.unbound
