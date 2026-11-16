"""Directory identity picker routes: search, refresh, sync trigger and status."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from akashic.models.audit_event import AuditEvent
from akashic.models.source import Source
from akashic.models.user import User
from akashic.models.directory import DirectoryEntry, DirectorySyncRun
from akashic.services.directory import sync
from akashic.services.directory.records import DirectoryError, DirRecord

AD_CFG = {"ldap_server": "ldap://x", "ldap_user_base": "dc=x"}
AK_CFG = {"authentik_url": "https://a", "authentik_api_token": "t"}
SID = "S-1-5-21-1-2-3-1001"


def now():
    return datetime.now(timezone.utc)


def entry(ref, backend="ad", kind="person", **kw):
    kw.setdefault("display_name", ref)
    kw.setdefault("groups_status", "none")
    kw.setdefault("synced_at", now())
    return DirectoryEntry(backend=backend, ref=ref, kind=kind, **kw)


async def _login(client, username="admin"):
    await client.post("/api/v1/users/register", json={"username": username, "password": "testpass123"})
    r = await client.post("/api/v1/users/login", json={"username": username, "password": "testpass123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def admin(client):
    return await _login(client)


@pytest_asyncio.fixture
async def viewer(client, admin):
    await client.post("/api/v1/users/create", json={"username": "viewer1", "password": "testpass123"}, headers=admin)
    r = await client.post("/api/v1/users/login", json={"username": "viewer1", "password": "testpass123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def gated_sync(setup_db, monkeypatch):
    """Sync fetches block on an Event; teardown releases the gate and awaits the
    tasks while the test session factory is still patched in."""
    monkeypatch.setattr(sync, "async_session", setup_db)
    gate = asyncio.Event()

    async def fetch(backend, stop=None):
        await gate.wait()
        return []
    monkeypatch.setattr(sync, "_fetch", fetch)
    yield gate
    gate.set()
    await asyncio.gather(*list(sync._tasks))


@pytest_asyncio.fixture(autouse=True)
async def no_leaked_sync_tasks():
    """Backstop: a test that starts a sync must drain it itself."""
    yield
    assert not sync._tasks, "sync tasks left running"


# ── search ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_identities_search_shape(client, admin, db_session, rt_set):
    rt_set(**AD_CFG)
    db_session.add(entry(
        "g1", display_name="Alice Smith", username="alice", email="alice@x.test",
        sid=SID, dn="CN=Alice,DC=x"))
    await db_session.commit()
    r = await client.get("/api/v1/access/identities?q=alice", headers=admin)
    assert r.status_code == 200
    body = r.json()
    assert len(body["results"]) == 1
    res = body["results"][0]
    assert res["id"] == f"sid:{SID}" and res["kind"] == "person"
    assert res["display_name"] == "Alice Smith" and res["username"] == "alice"
    assert res["email"] == "alice@x.test" and res["sid"] == SID and res["dn"] == "CN=Alice,DC=x"
    assert res["backends"] == [{"backend": "ad", "ref": "g1"}]
    assert res["match_quality"] == "exact" and res["possible_match_of"] == []
    assert res["akashic_user_id"] is None and res["has_fs_identity"] is True
    assert {s["backend"] for s in body["backends_status"]} == {"ad", "authentik", "akashic"}
    assert len(body["backends_status"]) == 3


@pytest.mark.asyncio
async def test_identities_requires_q(client, admin):
    assert (await client.get("/api/v1/access/identities?q=", headers=admin)).status_code == 422
    assert (await client.get("/api/v1/access/identities", headers=admin)).status_code == 422


@pytest.mark.asyncio
async def test_identities_viewer_forbidden(client, viewer):
    assert (await client.get("/api/v1/access/identities?q=a", headers=viewer)).status_code == 403
    assert (await client.get("/api/v1/access/directory/status", headers=viewer)).status_code == 403
    assert (await client.post("/api/v1/access/directory/sync", headers=viewer)).status_code == 403
    r = await client.post("/api/v1/access/identities/refresh", json={"id": "sid:x"}, headers=viewer)
    assert r.status_code == 403


# ── sync ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_202_then_status_running(client, admin, rt_set, gated_sync):
    rt_set(**AD_CFG)
    r = await client.post("/api/v1/access/directory/sync", headers=admin)
    assert r.status_code == 202
    runs = r.json()["runs"]
    assert [(x["backend"], x["status"], x["trigger"]) for x in runs] == [("ad", "running", "manual")]
    st = (await client.get("/api/v1/access/directory/status", headers=admin)).json()
    ad = next(b for b in st["backends"] if b["backend"] == "ad")
    assert ad["configured"] and ad["running"]
    assert ad["last_run"]["id"] == runs[0]["id"]
    ak = next(b for b in st["backends"] if b["backend"] == "authentik")
    assert not ak["configured"] and not ak["running"] and ak["last_run"] is None


@pytest.mark.asyncio
async def test_sync_conflict_409(client, admin, rt_set, gated_sync):
    rt_set(**AD_CFG)
    assert (await client.post("/api/v1/access/directory/sync", headers=admin)).status_code == 202
    assert (await client.post("/api/v1/access/directory/sync", headers=admin)).status_code == 409


@pytest.mark.asyncio
async def test_sync_none_configured_400(client, admin, rt_set):
    rt_set()
    assert (await client.post("/api/v1/access/directory/sync", headers=admin)).status_code == 400


@pytest.mark.asyncio
async def test_sync_records_audit_event(client, admin, db_session, rt_set, gated_sync):
    rt_set(**AD_CFG)
    run_id = (await client.post("/api/v1/access/directory/sync", headers=admin)).json()["runs"][0]["id"]
    events = (await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "directory_sync"))).scalars().all()
    assert len(events) == 1
    assert events[0].payload == {"runs": [{"id": run_id, "backend": "ad"}]}


# ── refresh ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_unknown_404(client, admin):
    r = await client.post("/api/v1/access/identities/refresh", json={"id": "sid:S-1-5-nope"}, headers=admin)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_refresh_updates_groups(client, admin, db_session, rt_set, monkeypatch):
    rt_set(**AD_CFG)
    db_session.add(entry("p1", sid=SID, dn="CN=P,DC=x", display_name="Old"))
    await db_session.commit()

    def live(*, sid, dn, timeout):
        return DirRecord(
            backend="ad", ref="p1", kind="person", display_name="New", username="p",
            email=None, sid=SID, dn="CN=P,DC=x", group_sids=("S-1-5-21-1-2-3-513",),
            groups_status="ok")
    monkeypatch.setattr(sync, "fetch_ad_person", live)
    r = await client.post("/api/v1/access/identities/refresh", json={"id": f"sid:{SID}"}, headers=admin)
    assert r.status_code == 200
    assert r.json()["display_name"] == "New" and r.json()["groups_synced_at"] is not None
    row = (await db_session.execute(select(DirectoryEntry))).scalar_one()
    assert row.group_sids == ["S-1-5-21-1-2-3-513"]


@pytest.mark.asyncio
async def test_refresh_all_failed_502_redacted(client, admin, db_session, rt_set, monkeypatch):
    rt_set(**AD_CFG)
    db_session.add(entry("p1", sid=SID, display_name="Keep"))
    await db_session.commit()

    def down(*, sid, dn, timeout):
        raise DirectoryError("LDAP unreachable")
    monkeypatch.setattr(sync, "fetch_ad_person", down)
    r = await client.post("/api/v1/access/identities/refresh", json={"id": f"sid:{SID}"}, headers=admin)
    assert r.status_code == 502 and "LDAP unreachable" in r.json()["message"]
    row = (await db_session.execute(select(DirectoryEntry))).scalar_one()
    assert row.display_name == "Keep"


@pytest.mark.asyncio
async def test_refresh_person_gone_404(client, admin, db_session, rt_set, monkeypatch):
    rt_set(**AD_CFG)
    db_session.add(entry("p1", sid=SID))
    await db_session.commit()
    monkeypatch.setattr(sync, "fetch_ad_person", lambda *, sid, dn, timeout: None)
    r = await client.post("/api/v1/access/identities/refresh", json={"id": f"sid:{SID}"}, headers=admin)
    assert r.status_code == 404
    assert (await db_session.execute(select(DirectoryEntry))).scalars().all() == []


# ── status ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_next_run_at(client, admin, db_session, rt_set):
    rt_set(**AD_CFG, directory_sync_interval_minutes=60)
    t = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    db_session.add(DirectorySyncRun(
        backend="ad", status="succeeded", trigger="manual", started_at=t,
        finished_at=t + timedelta(minutes=2)))
    await db_session.commit()
    st = (await client.get("/api/v1/access/directory/status", headers=admin)).json()
    assert st["interval_minutes"] == 60
    assert datetime.fromisoformat(st["next_run_at"]) == t + timedelta(minutes=60)
    ad = next(b for b in st["backends"] if b["backend"] == "ad")
    assert datetime.fromisoformat(ad["last_success_at"]) == t + timedelta(minutes=2)
    assert ad["running"] is False

    rt_set(**AD_CFG, directory_sync_interval_minutes=0)
    st = (await client.get("/api/v1/access/directory/status", headers=admin)).json()
    assert st["interval_minutes"] == 0 and st["next_run_at"] is None


@pytest.mark.asyncio
async def test_status_next_run_at_now_when_never_run(client, admin, rt_set):
    rt_set(**AD_CFG, directory_sync_interval_minutes=60)
    before = now()
    st = (await client.get("/api/v1/access/directory/status", headers=admin)).json()
    assert before <= datetime.fromisoformat(st["next_run_at"]) <= now()


@pytest.mark.asyncio
async def test_refresh_rejects_groups_and_non_directory_identities(client, admin, db_session, rt_set):
    rt_set(**AD_CFG)
    db_session.add(entry("g1", kind="group", sid="S-1-5-21-1-2-3-513"))
    await db_session.commit()
    r = await client.post("/api/v1/access/identities/refresh", json={"id": "sid:S-1-5-21-1-2-3-513"}, headers=admin)
    assert r.status_code == 400 and "directory record" in r.json()["message"]
    local = User(username="local1")
    db_session.add(local)
    await db_session.commit()
    r = await client.post("/api/v1/access/identities/refresh", json={"id": f"akashic:{local.id}"}, headers=admin)
    assert r.status_code == 400 and "directory record" in r.json()["message"]


@pytest.mark.asyncio
async def test_source_limited_token_forbidden_on_directory_routes(client, admin, db_session):
    src = Source(name="s-lim", type="local", connection_config={"path": "/tmp"}, status="online")
    db_session.add(src)
    await db_session.commit()
    r = await client.post(
        "/api/v1/me/tokens", headers=admin,
        json={"name": "lim", "scopes": ["access:read"], "source_ids": [str(src.id)]})
    assert r.status_code == 201, r.text
    # A limited token cannot hold maintenance:write, so the sync route is
    # refused on scope; the other three are refused as not source-aware.
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    for method, path, kw in (
        ("GET", "/api/v1/access/identities?q=a", {}),
        ("POST", "/api/v1/access/identities/refresh", {"json": {"id": "sid:x"}}),
        ("POST", "/api/v1/access/directory/sync", {}),
        ("GET", "/api/v1/access/directory/status", {}),
    ):
        assert (await client.request(method, path, headers=h, **kw)).status_code == 403, path


@pytest.mark.asyncio
async def test_status_never_leaks_secrets(client, admin, db_session, rt_set, gated_sync, monkeypatch):
    pw, tok = "s3cr3t-bind-pw", "s3cr3t-ak-token"
    rt_set(**AD_CFG, authentik_url="https://a", ldap_bind_password=pw, authentik_api_token=tok)
    db_session.add(DirectorySyncRun(backend="ad", status="failed", trigger="manual", error="bind failed"))
    db_session.add(entry("p1", sid=SID))
    await db_session.commit()
    texts = []
    r = await client.post("/api/v1/access/directory/sync", headers=admin)
    assert r.status_code == 202
    texts.append(r.text)
    for path in ("/api/v1/access/directory/status", "/api/v1/access/identities?q=a"):
        texts.append((await client.get(path, headers=admin)).text)
    audit = (await db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == "directory_sync"))).scalars().all()
    texts.append(json.dumps([e.payload for e in audit]))

    def down(*, sid, dn, timeout):
        raise DirectoryError("LDAP unreachable")
    monkeypatch.setattr(sync, "fetch_ad_person", down)
    r = await client.post("/api/v1/access/identities/refresh", json={"id": f"sid:{SID}"}, headers=admin)
    assert r.status_code == 502
    texts.append(r.text)
    for text in texts:
        assert pw not in text and tok not in text
