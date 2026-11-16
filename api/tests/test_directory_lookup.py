"""Directory lookup: search, backend status, load and token resolution."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from akashic.models.directory import DirectoryEntry, DirectorySyncRun
from akashic.models.fs_person import FsBinding, FsPerson
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services.directory import lookup

AD_CFG = {"ldap_server": "ldap://x", "ldap_user_base": "dc=x"}
AK_CFG = {"authentik_url": "https://a", "authentik_api_token": "t"}


def now():
    return datetime.now(timezone.utc)


def entry(ref, backend="ad", kind="person", **kw):
    kw.setdefault("display_name", ref)
    kw.setdefault("groups_status", "none")
    kw.setdefault("synced_at", now())
    return DirectoryEntry(backend=backend, ref=ref, kind=kind, **kw)


async def add_user(db, username, email=None, **kw):
    u = User(username=username, email=email, **kw)
    db.add(u)
    await db.flush()
    return u


async def add_binding(db, user, identity_type, identifier, groups=()):
    src = Source(name=f"s-{uuid.uuid4().hex[:8]}", type="local",
                 connection_config={"path": "/tmp"}, status="online")
    p = FsPerson(user_id=user.id, label="p")
    db.add_all([src, p])
    await db.flush()
    db.add(FsBinding(fs_person_id=p.id, source_id=src.id, identity_type=identity_type,
                     identifier=identifier, groups=list(groups)))
    await db.flush()


@pytest.mark.asyncio
async def test_search_escapes_like_metachars(db_session):
    db_session.add_all([entry("a", display_name="50%_off"), entry("b", display_name="500ff")])
    await db_session.commit()
    out = await lookup.search_identities(db_session, "50%_", "any", 10)
    assert [i.display_name for i in out] == ["50%_off"]


@pytest.mark.asyncio
async def test_search_merges_cache_and_akashic_users(db_session):
    dn = "CN=Al,DC=x"
    db_session.add(entry("g1", display_name="Al", username="al", dn=dn, sid="S-1-5-21-1"))
    u = await add_user(db_session, "al-ldap", auth_provider="ldap", external_id=dn.lower())
    await db_session.commit()
    out = await lookup.search_identities(db_session, "al", "any", 10)
    assert len(out) == 1
    assert out[0].akashic_user_id == u.id and out[0].backends == ["ad", "akashic"]
    assert out[0].id == "sid:S-1-5-21-1"


@pytest.mark.asyncio
async def test_search_kind_filter_and_limit(db_session):
    db_session.add_all([
        entry("p1", display_name="zed one"), entry("p2", display_name="zed two"),
        entry("p3", display_name="zed three"),
        entry("g1", kind="group", display_name="zed group"),
    ])
    await db_session.commit()
    groups = await lookup.search_identities(db_session, "zed", "group", 10)
    assert [i.display_name for i in groups] == ["zed group"]
    people = await lookup.search_identities(db_session, "zed", "person", 2)
    assert len(people) == 2 and all(i.kind == "person" for i in people)
    assert [i.display_name for i in people] == ["zed one", "zed three"]


@pytest.mark.asyncio
async def test_search_sorts_exact_before_prefix(db_session):
    db_session.add_all([
        entry("a", display_name="Zoe Bob", username="zb"),
        entry("b", display_name="Bob", username="zzz"),
        entry("c", display_name="Bobby", username="bobby"),
        entry("d", display_name="Abob", username="abob"),
    ])
    await db_session.commit()
    out = await lookup.search_identities(db_session, "bob", "any", 10)
    assert [i.display_name for i in out] == ["Bob", "Bobby", "Abob", "Zoe Bob"]


@pytest.mark.asyncio
async def test_backends_status(db_session, rt_set):
    rt_set(**AK_CFG)
    st = {s["backend"]: s for s in await lookup.backends_status(db_session)}
    assert st["ad"]["status"] == "not_configured"
    assert st["authentik"]["status"] == "unavailable" and st["authentik"]["detail"] == "never synced"
    assert st["akashic"]["status"] == "ok"
    db_session.add(DirectorySyncRun(backend="authentik", status="failed", trigger="manual",
                                    error="boom", finished_at=now()))
    await db_session.commit()
    st = {s["backend"]: s for s in await lookup.backends_status(db_session)}
    assert st["authentik"] == {"backend": "authentik", "status": "unavailable", "detail": "boom"}
    db_session.add(DirectorySyncRun(backend="authentik", status="succeeded", trigger="manual",
                                    started_at=now() + timedelta(seconds=5), finished_at=now()))
    await db_session.commit()
    st = {s["backend"]: s for s in await lookup.backends_status(db_session)}
    assert st["authentik"]["status"] == "ok"


@pytest.mark.asyncio
async def test_load_identity_unknown_returns_none(db_session):
    assert await lookup.load_identity(db_session, "sid:S-1-nope") is None
    assert await lookup.load_identity(db_session, "akashic:not-a-uuid") is None
    assert await lookup.load_identity(db_session, "bogus") is None


async def _person_with_binding(db, **ad_kw):
    ad_kw.setdefault("groups_status", "ok")
    ad_kw.setdefault("groups_synced_at", now())
    ad_kw.setdefault("group_sids", ["G1"])
    db.add(entry("g1", sid="S1", email="a@x.io", username="alice",
                 uid_number=1000, gid_number=100, **ad_kw))
    u = await add_user(db, "alice", email="a@x.io")
    await add_binding(db, u, "nfsv4_principal", "alice")
    await db.commit()
    return await lookup.load_identity(db, "sid:S1")


@pytest.mark.asyncio
async def test_resolve_person_tokens_ok(db_session):
    ident = await _person_with_binding(db_session)
    r = await lookup.resolve_tokens(db_session, ident)
    assert r.status == "ok" and r.missing_identity_kinds == []
    assert set(r.tokens) == {
        "sid:S1", "sid:G1", "posix:uid:1000", "posix:gid:100", "cloud_drive:user:a@x.io",
        "cloud_drive:domain:x.io", "nfsv4:alice", "*", "auth"}
    assert r.tokens == sorted(r.tokens)
    origin = {u.token: u.origin for u in r.used}
    assert origin["sid:S1"] == "ad" and origin["sid:G1"] == "ad" and origin["posix:uid:1000"] == "ad"
    assert origin["cloud_drive:user:a@x.io"] == "email"
    assert origin["nfsv4:alice"] == "akashic_binding"
    assert origin["*"] == origin["auth"] == "builtin"
    assert ident.has_fs_identity is True


@pytest.mark.asyncio
async def test_resolve_cached_when_stale(db_session):
    gs = now() - timedelta(hours=1)
    ident = await _person_with_binding(db_session, synced_at=gs, groups_synced_at=gs)
    r = await lookup.resolve_tokens(db_session, ident)
    assert r.status == "cached" and r.cached_at == gs


@pytest.mark.asyncio
async def test_resolve_cached_when_group_read_failed_with_old_groups(db_session):
    gs = now() - timedelta(hours=1)
    ident = await _person_with_binding(db_session, groups_status="failed", groups_synced_at=gs)
    r = await lookup.resolve_tokens(db_session, ident)
    assert r.status == "cached" and r.cached_at == gs and "sid:G1" in r.tokens


@pytest.mark.asyncio
async def test_resolve_expansion_partial_when_groups_failed(db_session):
    ident = await _person_with_binding(db_session, groups_status="failed", groups_synced_at=None,
                                        group_sids=[])
    r = await lookup.resolve_tokens(db_session, ident)
    assert r.status == "partial" and "sid:G1" not in r.tokens and "sid:S1" in r.tokens
    assert r.detail


@pytest.mark.asyncio
async def test_resolve_group_includes_parent_groups(db_session):
    db_session.add(entry("grp", kind="group", sid="G1", group_sids=["P1"], groups_status="ok",
                         groups_synced_at=now()))
    await db_session.commit()
    ident = await lookup.load_identity(db_session, "sid:G1")
    r = await lookup.resolve_tokens(db_session, ident)
    assert r.tokens == ["sid:G1", "sid:P1"] and r.status == "ok"


@pytest.mark.asyncio
async def test_resolve_names_from_group_rows(db_session):
    db_session.add(entry("grp", kind="group", display_name="Parents", sid="P1"))
    db_session.add(entry("grp2", kind="group", sid="G1", group_sids=["P1"], groups_status="ok",
                         groups_synced_at=now()))
    await db_session.commit()
    r = await lookup.resolve_tokens(db_session, await lookup.load_identity(db_session, "sid:G1"))
    assert {u.token: u.name for u in r.used}["sid:P1"] == "Parents"


@pytest.mark.asyncio
async def test_resolve_no_sid_identity(db_session):
    db_session.add(entry("42", backend="authentik", email="B@x.io", username="bob"))
    await db_session.commit()
    ident = await lookup.load_identity(db_session, "email:b@x.io")
    r = await lookup.resolve_tokens(db_session, ident)
    assert r.tokens == ["*", "auth", "cloud_drive:domain:x.io", "cloud_drive:user:b@x.io"]
    assert r.missing_identity_kinds == ["posix", "sid"]
    assert ident.has_fs_identity is False and r.status == "failed"


@pytest.mark.asyncio
async def test_search_and_load_see_same_cluster(db_session):
    db_session.add_all([
        entry("h", display_name="Hxq", dn="CN=H,DC=x"),
        entry("1", backend="authentik", dn="cn=h,dc=x", email="e@x.io"),
        entry("2", backend="authentik", email="E@x.io", sid="S-1-2"),
    ])
    await db_session.commit()
    (found,) = await lookup.search_identities(db_session, "Hxq", "any", 10)
    assert found.id == "sid:S-1-2" and len(found.records) == 3
    loaded = await lookup.load_identity(db_session, found.id)
    assert loaded.id == found.id and {r.ref for r in loaded.records} == {"h", "1", "2"}
    by_seed = await lookup.load_identity(db_session, "dn:cn=h,dc=x")
    assert by_seed.id == found.id


@pytest.mark.asyncio
async def test_resolve_counts_every_akashic_user_binding(db_session):
    db_session.add(entry("g1", sid="S1", email="a@x.io", groups_status="ok",
                         groups_synced_at=now()))
    u1 = await add_user(db_session, "u1", email="a@x.io")
    u2 = await add_user(db_session, "u2", email="A@x.io")
    await add_binding(db_session, u1, "nfsv4_principal", "one")
    await add_binding(db_session, u2, "nfsv4_principal", "two")
    await db_session.commit()
    ident = await lookup.load_identity(db_session, "sid:S1")
    r = await lookup.resolve_tokens(db_session, ident)
    assert {"nfsv4:one", "nfsv4:two"} <= set(r.tokens)


@pytest.mark.asyncio
async def test_resolve_partial_for_akashic_sid_without_ad_row(db_session):
    u = await add_user(db_session, "solo")
    await add_binding(db_session, u, "sid", "S-1-9")
    await db_session.commit()
    ident = await lookup.load_identity(db_session, f"akashic:{u.id}")
    r = await lookup.resolve_tokens(db_session, ident)
    assert "sid:S-1-9" in r.tokens and r.status == "partial"


@pytest.mark.asyncio
async def test_resolve_truncated_cluster_is_partial(db_session):
    db_session.add(entry("seed", sid="S1", email="e@x.io", groups_status="ok",
                         groups_synced_at=now()))
    db_session.add_all([entry(f"x{i}", email="e@x.io") for i in range(lookup.LOAD_MAX_RECORDS + 10)])
    await db_session.commit()
    ident = await lookup.load_identity(db_session, "sid:S1")
    assert ident.truncated and len(ident.records) == lookup.LOAD_MAX_RECORDS
    r = await lookup.resolve_tokens(db_session, ident)
    assert r.status == "partial" and "cap" in r.detail


@pytest.mark.asyncio
async def test_resolve_row_without_freshness_is_not_ok(db_session):
    ident = await _person_with_binding(db_session)
    ident.freshness = {}
    r = await lookup.resolve_tokens(db_session, ident)
    assert r.status == "cached"


@pytest.mark.asyncio
async def test_resolve_ignores_malformed_email(db_session):
    db_session.add(entry("g1", sid="S1", email="nodomain", groups_status="ok",
                         groups_synced_at=now()))
    await db_session.commit()
    r = await lookup.resolve_tokens(db_session, await lookup.load_identity(db_session, "sid:S1"))
    assert not any(t.startswith("cloud_drive:") for t in r.tokens)


@pytest.mark.asyncio
async def test_user_merges_through_second_sid_binding(db_session):
    db_session.add(entry("g1", sid="S-1-B", groups_status="ok", groups_synced_at=now()))
    u = await add_user(db_session, "multi")
    await add_binding(db_session, u, "sid", "S-1-A")
    await add_binding(db_session, u, "sid", "S-1-B")
    await db_session.commit()
    ident = await lookup.load_identity(db_session, "sid:S-1-B")
    assert ident.backends == ["ad", "akashic"] and ident.id == "sid:S-1-A"


@pytest.mark.asyncio
async def test_load_truncates_when_seed_query_exceeds_cap(db_session):
    db_session.add_all([entry(f"x{i}", email="e@x.io", sid=f"S-{i:03}", groups_status="ok",
                              groups_synced_at=now())
                        for i in range(lookup.LOAD_MAX_RECORDS + 11)])
    await db_session.commit()
    ident = await lookup.load_identity(db_session, "email:e@x.io")
    assert ident.truncated and len(ident.records) <= lookup.LOAD_MAX_RECORDS
    r = await lookup.resolve_tokens(db_session, ident)
    assert r.status == "partial"


@pytest.mark.asyncio
async def test_search_query_count_is_bounded(db_session):
    db_session.add_all([entry(f"n{i}", display_name=f"needle {i}", sid=f"S-{i}", email=f"n{i}@x.io")
                        for i in range(12)])
    await db_session.commit()
    stmts = []
    from sqlalchemy import event
    bind = db_session.get_bind()
    eng = getattr(bind, "sync_engine", bind)
    hook = lambda *a: stmts.append(a[2])  # noqa: E731
    event.listen(eng, "before_cursor_execute", hook)
    try:
        out = await lookup.search_identities(db_session, "needle", "any", 20)
    finally:
        event.remove(eng, "before_cursor_execute", hook)
    assert len(out) == 12 and len(stmts) <= 15
    assert await lookup.load_identity(db_session, out[0].id) is not None


@pytest.mark.asyncio
async def test_load_prefers_person_for_shared_email(db_session):
    db_session.add_all([entry("p", email="s@x.io"), entry("g", kind="group", email="s@x.io")])
    await db_session.commit()
    ident = await lookup.load_identity(db_session, "email:s@x.io")
    assert ident.kind == "person"


@pytest.mark.asyncio
async def test_akashic_user_ids_deterministic(db_session):
    u1 = await add_user(db_session, "ua", email="m@x.io")
    u2 = await add_user(db_session, "ub", email="m@x.io")
    await db_session.commit()
    ident = await lookup.load_identity(db_session, "email:m@x.io")
    assert ident.akashic_user_ids == sorted([u1.id, u2.id])
    assert ident.akashic_user_id == min(u1.id, u2.id)
