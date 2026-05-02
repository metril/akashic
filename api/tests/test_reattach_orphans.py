"""POST /api/sources/{id}/reattach-orphans + the orphan-matcher.

End-to-end shape:
  1. Seed source A + 3 entries → delete (preserve) → 3 orphans.
  2. Seed source B at the same paths → 3 fresh entries.
  3. Dry-run reattach → returns matched=3, no DB change.
  4. Commit reattach → orphans land on source B; fresh duplicates
     deleted; orphan ids preserved (history follows them).
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User


@pytest_asyncio.fixture
async def admin_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(), username="admin", email="a@b.c",
            password_hash="x", role="admin",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def _admin_client(setup_db, user: User) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_get_current_user():
        return user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _stub_meili(monkeypatch):
    from akashic.services import search

    async def _noop(*_args, **_kw):
        pass

    monkeypatch.setattr(search, "delete_files_batch", _noop)
    monkeypatch.setattr(search, "update_files_partial", _noop)


async def _seed_source(setup_db, name: str, paths: list[tuple[str, str | None]]) -> uuid.UUID:
    """Create a source and one Entry per (path, hash) pair."""
    async with setup_db() as db:
        src = Source(
            id=uuid.uuid4(), name=name,
            type="local", connection_config={"path": "/tmp"},
        )
        db.add(src)
        await db.flush()
        for path, content_hash in paths:
            db.add(Entry(
                id=uuid.uuid4(),
                source_id=src.id,
                kind="file",
                parent_path="/tmp",
                path=path,
                name=path.rsplit("/", 1)[-1],
                size_bytes=100,
                content_hash=content_hash,
            ))
        await db.commit()
        return src.id


@pytest.mark.asyncio
async def test_dry_run_then_commit_reattaches(setup_db, admin_user):
    # Source A with 3 files → delete (preserve) → 3 orphans.
    a_paths = [("/tmp/file-1.txt", "h1"), ("/tmp/file-2.txt", "h2"),
               ("/tmp/file-3.txt", "h3")]
    src_a = await _seed_source(setup_db, "src-a", a_paths)
    async with _admin_client(setup_db, admin_user) as ac:
        await ac.delete(f"/api/sources/{src_a}")

    # Snapshot orphan ids — these MUST survive the reattach (the
    # whole point is that history follows the orphan, not a fresh row).
    async with setup_db() as db:
        orphan_ids_before = sorted(
            (await db.execute(select(Entry.id))).scalars().all()
        )
    assert len(orphan_ids_before) == 3

    # Source B at the same paths.
    src_b = await _seed_source(setup_db, "src-b", a_paths)

    # Dry-run.
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.post(
            f"/api/sources/{src_b}/reattach-orphans",
            json={"strategy": "path", "dry_run": True},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] == 3
    assert body["conflicts"] == 0
    assert body["ambiguous"] == 0
    assert body["committed"] is False

    # DB unchanged after dry-run.
    async with setup_db() as db:
        unchanged = sorted(
            (await db.execute(select(Entry.id))).scalars().all()
        )
        assert len(unchanged) == 6  # 3 orphans + 3 fresh

    # Commit.
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.post(
            f"/api/sources/{src_b}/reattach-orphans",
            json={"strategy": "path", "dry_run": False},
        )
    assert r.status_code == 200
    assert r.json()["committed"] is True
    assert r.json()["matched"] == 3

    # Post-commit: 3 entries total, all owned by src_b, all
    # carrying the original orphan ids (history followed them).
    async with setup_db() as db:
        rows = (await db.execute(select(Entry))).scalars().all()
        assert len(rows) == 3
        assert all(r.source_id == src_b for r in rows)
        post_ids = sorted(r.id for r in rows)
        assert post_ids == orphan_ids_before


@pytest.mark.asyncio
async def test_path_and_hash_excludes_mismatched(setup_db, admin_user):
    src_a = await _seed_source(setup_db, "src-a", [
        ("/tmp/same.txt", "h1"),
        ("/tmp/changed.txt", "h-old"),
    ])
    async with _admin_client(setup_db, admin_user) as ac:
        await ac.delete(f"/api/sources/{src_a}")

    src_b = await _seed_source(setup_db, "src-b", [
        ("/tmp/same.txt", "h1"),       # hash matches
        ("/tmp/changed.txt", "h-new"), # hash differs → conflict
    ])

    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.post(
            f"/api/sources/{src_b}/reattach-orphans",
            json={"strategy": "path_and_hash", "dry_run": True},
        )
    body = r.json()
    assert body["matched"] == 1
    assert body["conflicts"] == 1


@pytest.mark.asyncio
async def test_ambiguous_paths_are_skipped(setup_db, admin_user):
    """Two orphans share a path with a single fresh entry → ambiguous,
    not auto-matched.

    Realistic scenario: two distinct sources both got orphaned (e.g.
    overlapping mounts at /tmp/x.txt), then a third source is
    re-created at the same path. The matcher can't pick which orphan
    "deserves" the re-attach, so it surfaces both as ambiguous and
    leaves them alone."""
    src_a = await _seed_source(setup_db, "src-a", [("/tmp/x.txt", None)])
    src_a2 = await _seed_source(setup_db, "src-a2", [("/tmp/x.txt", None)])
    async with _admin_client(setup_db, admin_user) as ac:
        await ac.delete(f"/api/sources/{src_a}")
        await ac.delete(f"/api/sources/{src_a2}")
    src_b = await _seed_source(setup_db, "src-b", [("/tmp/x.txt", None)])

    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.post(
            f"/api/sources/{src_b}/reattach-orphans",
            json={"strategy": "path", "dry_run": True},
        )
    body = r.json()
    assert body["ambiguous"] == 1
    assert body["matched"] == 0


@pytest.mark.asyncio
async def test_orphan_match_count_endpoint(setup_db, admin_user):
    src_a = await _seed_source(setup_db, "src-a", [
        ("/tmp/1.txt", None), ("/tmp/2.txt", None), ("/tmp/3.txt", None),
    ])
    async with _admin_client(setup_db, admin_user) as ac:
        await ac.delete(f"/api/sources/{src_a}")
    src_b = await _seed_source(setup_db, "src-b", [
        ("/tmp/1.txt", None), ("/tmp/2.txt", None),
        # Note: /tmp/3.txt absent → that orphan stays unattached.
    ])
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.get(f"/api/sources/{src_b}/orphan-match-count")
    assert r.status_code == 200
    assert r.json() == {"count": 2}


@pytest.mark.asyncio
async def test_reattach_404_for_unknown_source(setup_db, admin_user):
    bogus = uuid.uuid4()
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.post(
            f"/api/sources/{bogus}/reattach-orphans",
            json={"strategy": "path", "dry_run": True},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_reattach_400_for_bad_strategy(setup_db, admin_user):
    src = await _seed_source(setup_db, "x", [])
    async with _admin_client(setup_db, admin_user) as ac:
        r = await ac.post(
            f"/api/sources/{src}/reattach-orphans",
            json={"strategy": "fingerprint_chase", "dry_run": True},
        )
    assert r.status_code == 400
    assert "fingerprint_chase" in r.text
