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
from akashic.config import settings
from akashic.models.entry import Entry
from akashic.models.fs_person import FsBinding, FsPerson
from akashic.models.source import Source
from akashic.models.user import SourcePermission, User


@pytest.fixture
def enforce_perms(monkeypatch):
    """Flip the feature flag for a single test. Resets on teardown."""
    monkeypatch.setattr(settings, "browse_enforce_perms", True)
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


async def _grant_source_access(db_session: AsyncSession, user: User, source_id: uuid.UUID) -> None:
    """Non-admin users need an explicit SourcePermission to call /api/browse.
    Phase 5's per-user trim runs *after* the source-level check passes."""
    db_session.add(SourcePermission(user_id=user.id, source_id=source_id, access_level="read"))
    await db_session.commit()


@pytest.mark.asyncio
async def test_browse_unfiltered_when_flag_off(client: AsyncClient, db_session: AsyncSession):
    """Default deployment behaviour — flag off, every entry visible."""
    source, entries = await _seed_three_entries(db_session)
    user = User(id=uuid.uuid4(), username="viewer1", email="v@e", password_hash="x", role="user")
    db_session.add(user)
    await db_session.commit()
    await _grant_source_access(db_session, user, source.id)
    await _bind_user_to_uid(db_session, user, source.id, "9999")  # uid that doesn't match any entry
    token = create_access_token({"sub": str(user.id)})

    resp = await client.get(
        f"/api/browse?source_id={source.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    paths = sorted(e["path"] for e in resp.json()["entries"])
    assert paths == ["/a", "/b", "/c"]


@pytest.mark.asyncio
async def test_browse_filters_when_flag_on_and_user_has_bindings(
    enforce_perms, client: AsyncClient, db_session: AsyncSession,
):
    """Flag on + binding for uid 1000 → see /a (uid:1000) and /c (`*`)."""
    source, _ = await _seed_three_entries(db_session)
    user = User(id=uuid.uuid4(), username="alice", email="a@e", password_hash="x", role="user")
    db_session.add(user)
    await db_session.commit()
    await _grant_source_access(db_session, user, source.id)
    await _bind_user_to_uid(db_session, user, source.id, "1000")
    token = create_access_token({"sub": str(user.id)})

    resp = await client.get(
        f"/api/browse?source_id={source.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    paths = sorted(e["path"] for e in resp.json()["entries"])
    assert paths == ["/a", "/c"]  # /b filtered (uid:2000 is not us)


@pytest.mark.asyncio
async def test_browse_unbound_user_sees_all_even_when_flag_on(
    enforce_perms, client: AsyncClient, db_session: AsyncSession,
):
    """A user with zero FsBindings keeps see-all behaviour. The flag is a
    deployment switch, not a per-user lockout — admins must attach
    bindings before the trim takes effect."""
    source, _ = await _seed_three_entries(db_session)
    user = User(id=uuid.uuid4(), username="newcomer", email="n@e", password_hash="x", role="user")
    db_session.add(user)
    await db_session.commit()
    await _grant_source_access(db_session, user, source.id)
    token = create_access_token({"sub": str(user.id)})

    resp = await client.get(
        f"/api/browse?source_id={source.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    paths = sorted(e["path"] for e in resp.json()["entries"])
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
        f"/api/browse?source_id={source.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sorted(e["path"] for e in resp.json()["entries"]) == ["/a", "/c"]

    # show_all=1 — no filter regardless of bindings.
    resp = await client.get(
        f"/api/browse?source_id={source.id}&show_all=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert sorted(e["path"] for e in resp.json()["entries"]) == ["/a", "/b", "/c"]


@pytest.mark.asyncio
async def test_get_entry_returns_404_for_unviewable(
    enforce_perms, client: AsyncClient, db_session: AsyncSession,
):
    """URL-guessing the id of a hidden entry must return 404 — 403 would
    leak existence ('the row exists, you just can't see it')."""
    source, entries = await _seed_three_entries(db_session)
    hidden = next(e for e in entries if e.path == "/b")
    user = User(id=uuid.uuid4(), username="u1", email="u1@e", password_hash="x", role="user")
    db_session.add(user)
    await db_session.commit()
    await _grant_source_access(db_session, user, source.id)
    await _bind_user_to_uid(db_session, user, source.id, "1000")
    token = create_access_token({"sub": str(user.id)})

    resp = await client.get(
        f"/api/entries/{hidden.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_effective_counts_reports_visible_and_hidden(
    enforce_perms, client: AsyncClient, db_session: AsyncSession,
):
    source, _ = await _seed_three_entries(db_session)
    user = User(id=uuid.uuid4(), username="u2", email="u2@e", password_hash="x", role="user")
    db_session.add(user)
    await db_session.commit()
    await _grant_source_access(db_session, user, source.id)
    await _bind_user_to_uid(db_session, user, source.id, "1000")
    token = create_access_token({"sub": str(user.id)})

    resp = await client.get(
        f"/api/browse/effective-counts?source_id={source.id}",
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
    user = User(id=uuid.uuid4(), username="u3", email="u3@e", password_hash="x", role="user")
    db_session.add(user)
    await db_session.commit()
    await _grant_source_access(db_session, user, source.id)
    token = create_access_token({"sub": str(user.id)})

    resp = await client.get(
        f"/api/browse/effective-counts?source_id={source.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enforced"] is False
    assert body["hidden"] == 0
    assert body["visible"] == 3
