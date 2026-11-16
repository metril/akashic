"""Identity merge: union-find over sid/dn/email within one kind."""
from akashic.services.directory.merge import merge_id, merge_identities
from akashic.services.directory.records import DirRecord


def rec(backend="ad", ref="r", kind="person", **kw):
    kw.setdefault("display_name", ref)
    kw.setdefault("username", None)
    kw.setdefault("email", None)
    kw.setdefault("sid", None)
    kw.setdefault("dn", None)
    return DirRecord(backend=backend, ref=ref, kind=kind, **kw)


def test_merge_by_sid_dn_email():
    out = merge_identities([
        rec("ad", "g1", sid="S1", email="A@x.io"),
        rec("authentik", "42", sid="S1"),
        rec("akashic", "u1", email="a@X.io"),
    ])
    assert len(out) == 1
    assert out[0].id == "sid:S1"
    assert out[0].backends == ["ad", "akashic", "authentik"]
    assert out[0].email == "A@x.io"


def test_merge_dn_case_insensitive():
    out = merge_identities([
        rec("ad", "a", dn="CN=A,DC=x"), rec("authentik", "1", dn="cn=a,dc=X"),
    ])
    assert len(out) == 1 and out[0].id == "dn:cn=a,dc=x"


def test_merge_never_crosses_kind():
    out = merge_identities([
        rec("ad", "p", kind="person", email="a@x.io"),
        rec("ad", "g", kind="group", email="a@x.io"),
    ])
    assert sorted(i.kind for i in out) == ["group", "person"]


def test_username_only_is_possible_match():
    out = merge_identities([
        rec("ad", "a", username="jdoe", sid="S1"),
        rec("akashic", "u1", username="JDoe"),
    ])
    assert len(out) == 2
    assert all(i.match_quality == "possible" for i in out)
    a, b = out
    assert a.possible_match_of == [b.id] and b.possible_match_of == [a.id]


def test_exact_when_no_username_overlap():
    out = merge_identities([rec("ad", "a", username="x", sid="S1")])
    assert out[0].match_quality == "exact" and out[0].possible_match_of == []


def test_merge_id_fallbacks():
    assert merge_id([rec(dn="CN=A,DC=x")]) == "dn:cn=a,dc=x"
    assert merge_id([rec(email="A@x.io")]) == "email:a@x.io"
    assert merge_id([rec("authentik", "42")]) == "authentik:42"
    assert merge_id([rec(sid="S2"), rec(sid="S1")]) == "sid:S1"


def test_alt_sids_are_merge_keys():
    out = merge_identities([
        rec("ad", "a", sid="S2"), rec("akashic", "u", sid="S1", alt_sids=("S2",)),
    ])
    assert len(out) == 1 and out[0].id == "sid:S1"


def test_has_fs_identity_counts_gid_only():
    (i,) = merge_identities([rec("ad", "a", gid_number=100)])
    assert i.has_fs_identity is True
