"""Phase 5 — Browse / GET-by-id permission filter behaviour.

Coverage:
- Feature flag off → no change in behaviour (regression guard).
- Feature flag on, user has no bindings → still sees everything (legacy
  users keep working until an admin attaches a binding).
- Feature flag on, user has bindings → sees only entries whose
  `viewable_by_read` overlaps their token set.
- Admin sees everything regardless; `show_all=1` is the explicit
  override knob (does nothing extra for admins; relevant once role
  semantics evolve).
- Direct URL guess on a hidden entry returns 404, not 403.
- /effective-counts returns the right visible/hidden split.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import create_access_token
from akashic.models.entry import Entry
from akashic.models.fs_person import FsBinding, FsPerson
from akashic.models.source import Source
from akashic.models.user import User


@pytest.fixture
def enforce_perms(rt_set):
    """Flip the feature flag for a single test. Resets on teardown."""
    rt_set(browse_enforce_perms=True)
    yield


async def _seed_three_entries(db_session: AsyncSession) -> tuple[Source, list[Entry]]:
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add(source)
    await db_session.commit()
    entries = [
        Entry(
            id=uuid.uuid4(), source_id=source.id, kind="file",
            parent_path="/", path="/a", name="a",
            viewable_by_read=["posix:uid:1000"],
            viewable_by_write=[], viewable_by_delete=[],
        ),
        Entry(
            id=uuid.uuid4(), source_id=source.id, kind="file",
            parent_path="/", path="/b", name="b",
            viewable_by_read=["posix:uid:2000"],
            viewable_by_write=[], viewable_by_delete=[],
        ),
        Entry(
            id=uuid.uuid4(), source_id=source.id, kind="file",
            parent_path="/", path="/c", name="c",
            viewable_by_read=["*"],
            viewable_by_write=[], viewable_by_delete=[],
        ),
    ]
    db_session.add_all(entries)
    await db_session.commit()
    return source, entries


async def _bind_user_to_uid(
    db_session: AsyncSession, user: User, source_id: uuid.UUID, uid: str,
) -> None:
    person = FsPerson(id=uuid.uuid4(), user_id=user.id, label="t")
    db_session.add(person)
    await db_session.commit()
    binding = FsBinding(
        id=uuid.uuid4(), fs_person_id=person.id, source_id=source_id,
        identity_type="posix_uid", identifier=uid, groups=[],
        groups_source="manual",
    )
    db_session.add(binding)
    await db_session.commit()


@pytest.mark.asyncio
async def test_browse_unfiltered_when_flag_off(client: AsyncClient, db_session: AsyncSession):
    """Default deployment behaviour — flag off, every entry visible."""
    source, entries = await _seed_three_entries(db_session)
    user = User(id=uuid.uuid4(), username="viewer1", email="v@e", password_hash="x", role="viewer")
    db_session.add(user)
    await db_session.commit()
    await _bind_user_to_uid(db_session, user, source.id, "9999")  # uid that doesn't match any entry
    token = create_access_token({"sub": str(user.id)})

    resp = await client.get(
        f"/api/v1/browse?source_id={source.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    paths = sorted(e["path"] for e in resp.json()["items"])
    assert paths == ["/a", "/b", "/c"]


@pytest.mark.asyncio
async def test_browse_filters_when_flag_on_and_user_has_bindings(
    enforce_perms, client: AsyncClient, db_session: AsyncSession,
):
    """Flag on + binding for uid 1000 → see /a (uid:1000) and /c (`*`)."""
    source, _ = await _seed_three_entries(db_session)
    user = User(id=uuid.uuid4(), username="alice", email="a@e", password_hash="x", role="viewer")
    db_session.add(user)
    await db_session.commit()
    await _bind_user_to_uid(db_session, user, source.id, "1000")
    token = create_access_token({"sub": str(user.id)})

    resp = await client.get(
        f"/api/v1/browse?source_id={source.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    paths = sorted(e["path"] for e in resp.json()["items"])
    assert paths == ["/a", "/c"]  # /b filtered (uid:2000 is not us)


@pytest.mark.asyncio
async def test_browse_unbound_user_sees_all_even_when_flag_on(
    enforce_perms, client: AsyncClient, db_session: AsyncSession,
):
    """A user with zero FsBindings keeps see-all behaviour. The flag is a
    deployment switch, not a per-user lockout — admins must attach
    bindings before the trim takes effect."""
    source, _ = await _seed_three_entries(db_session)
    user = User(id=uuid.uuid4(), username="newcomer", email="n@e", password_hash="x", role="viewer")
    db_session.add(user)
    await db_session.commit()
    token = create_access_token({"sub": str(user.id)})

    resp = await client.get(
        f"/api/v1/browse?source_id={source.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    paths = sorted(e["path"] for e in resp.json()["items"])
    assert paths == ["/a", "/b", "/c"]


@pytest.mark.asyncio
async def test_admin_show_all_overrides_filter(
    enforce_perms, client: AsyncClient, db_session: AsyncSession,
):
    """Admins can opt into the trim (binding-driven) but `show_all=1`
    bypasses it for the privileged debug view."""
    source, _ = await _seed_three_entries(db_session)
    admin = User(id=uuid.uuid4(), username="adm", email="adm@e", password_hash="x", role="admin")
    db_session.add(admin)
    await db_session.commit()
    await _bind_user_to_uid(db_session, admin, source.id, "1000")
    token = create_access_token({"sub": str(admin.id)})

    # Default (no show_all) — admin's binding gets applied.
    resp = await client.get(
        f"/api/v1/browse?source_id={source.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sorted(e["path"] for e in resp.json()["items"]) == ["/a", "/c"]

    # show_all=1 — no filter regardless of bindings.
    resp = await client.get(
        f"/api/v1/browse?source_id={source.id}&show_all=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sorted(e["path"] for e in resp.json()["items"]) == ["/a", "/b", "/c"]


@pytest.mark.asyncio
async def test_get_entry_returns_404_for_unviewable(
    enforce_perms, client: AsyncClient, db_session: AsyncSession,
):
    """URL-guessing the id of a hidden entry must return 404 — 403 would
    leak existence ('the row exists, you just can't see it')."""
    source, entries = await _seed_three_entries(db_session)
    hidden = next(e for e in entries if e.path == "/b")
    user = User(id=uuid.uuid4(), username="u1", email="u1@e", password_hash="x", role="viewer")
    db_session.add(user)
    await db_session.commit()
    await _bind_user_to_uid(db_session, user, source.id, "1000")
    token = create_access_token({"sub": str(user.id)})

    resp = await client.get(
        f"/api/v1/entries/{hidden.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_effective_counts_reports_visible_and_hidden(
    enforce_perms, client: AsyncClient, db_session: AsyncSession,
):
    source, _ = await _seed_three_entries(db_session)
    user = User(id=uuid.uuid4(), username="u2", email="u2@e", password_hash="x", role="viewer")
    db_session.add(user)
    await db_session.commit()
    await _bind_user_to_uid(db_session, user, source.id, "1000")
    token = create_access_token({"sub": str(user.id)})

    resp = await client.get(
        f"/api/v1/browse/effective-counts?source_id={source.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"visible": 2, "hidden": 1, "enforced": True}


@pytest.mark.asyncio
async def test_effective_counts_when_flag_off(
    client: AsyncClient, db_session: AsyncSession,
):
    """Flag off → enforced=False, hidden=0 always."""
    source, _ = await _seed_three_entries(db_session)
    user = User(id=uuid.uuid4(), username="u3", email="u3@e", password_hash="x", role="viewer")
    db_session.add(user)
    await db_session.commit()
    token = create_access_token({"sub": str(user.id)})

    resp = await client.get(
        f"/api/v1/browse/effective-counts?source_id={source.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enforced"] is False
    assert body["hidden"] == 0
    assert body["visible"] == 3


async def _viewer(db_session, source, uid="1000"):
    user = User(id=uuid.uuid4(), username=f"v{uuid.uuid4().hex[:6]}", email="v@e", password_hash="x", role="viewer")
    db_session.add(user)
    await db_session.commit()
    await _bind_user_to_uid(db_session, user, source.id, uid)
    return {"Authorization": f"Bearer {create_access_token({'sub': str(user.id)})}"}


@pytest.mark.asyncio
async def test_search_permission_filter_all_forced_readable_for_viewer(
    enforce_perms, client: AsyncClient, db_session: AsyncSession,
):
    source, _ = await _seed_three_entries(db_session)
    h = await _viewer(db_session, source)
    resp = await client.get(
        "/api/v1/search?q=&mode=glob&permission_filter=all", headers=h,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    hits = body.get("items") or body.get("hits") or []
    assert "/b" not in [x["path"] for x in hits]


@pytest.mark.asyncio
async def test_entries_list_and_by_hash_filtered_for_viewer(
    enforce_perms, client: AsyncClient, db_session: AsyncSession,
):
    source, entries = await _seed_three_entries(db_session)
    for e in entries:
        e.content_hash = "h1"
    await db_session.commit()
    h = await _viewer(db_session, source)
    r = await client.get(f"/api/v1/entries?source_id={source.id}", headers=h)
    assert r.status_code == 200, r.text
    assert sorted(e["path"] for e in r.json()["items"]) == ["/a", "/c"]
    r = await client.get("/api/v1/entries/by-hash/h1", headers=h)
    assert sorted(e["path"] for e in r.json()) == ["/a", "/c"]


@pytest.mark.asyncio
async def test_largest_files_filtered_for_viewer(
    enforce_perms, client: AsyncClient, db_session: AsyncSession,
):
    source, entries = await _seed_three_entries(db_session)
    for i, e in enumerate(entries):
        e.size_bytes = 100 + i
    await db_session.commit()
    h = await _viewer(db_session, source)
    r = await client.get("/api/v1/analytics/largest-files", headers=h)
    assert r.status_code == 200, r.text
    assert sorted(e["path"] for e in r.json()) == ["/a", "/c"]
