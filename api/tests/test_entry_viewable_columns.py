"""Phase 4 — verify the `entries.viewable_by_*` columns and surrounding wiring.

Three things this file checks:

1. Ingest writes the columns at insert/update time.
2. The values written match `denormalize_acl(...)` exactly — no drift
   between the SQL sink and the Meili sink possible.
3. `viewable_clause(tokens, right)` filters the way overlap should: a row
   is returned iff the row's `viewable_by_<right>` shares at least one
   token with the caller's token list.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import create_access_token
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services.access_query import viewable_clause
from akashic.services.acl_denorm import denormalize_acl
from akashic.services.ingest import compute_viewable_buckets


@pytest.mark.asyncio
async def test_ingest_populates_viewable_columns_for_new_entry(
    client: AsyncClient, db_session: AsyncSession,
):
    user = User(id=uuid.uuid4(), username="ingestor", email="i@e", password_hash="x", role="admin")
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add_all([user, source])
    await db_session.commit()
    token = create_access_token({"sub": str(user.id)})

    payload = {
        "source_id": str(source.id),
        "scan_id": str(uuid.uuid4()),
        "entries": [{
            "path": "/tmp/foo", "name": "foo", "kind": "file",
            "mode": 0o644, "uid": 1000, "gid": 100,
            "acl": {
                "type": "posix",
                "entries": [{"tag": "user", "qualifier": "1234", "perms": "rwx"}],
                "default_entries": None,
            },
        }],
        "is_final": True,
    }
    resp = await client.post(
        "/api/ingest/batch", json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    e = (await db_session.execute(
        select(Entry).where(Entry.source_id == source.id)
    )).scalar_one()

    # Columns are populated, not NULL.
    assert e.viewable_by_read is not None
    assert e.viewable_by_write is not None
    assert e.viewable_by_delete is not None

    # Values match what `denormalize_acl` would compute from the same inputs —
    # no separate code path.
    expected = denormalize_acl(
        acl=None,  # the round-trip stores acl as a dict; recompute via helper
        base_mode=e.mode, base_uid=e.uid, base_gid=e.gid,
    ) if e.acl is None else compute_viewable_buckets(e.acl, e.mode, e.uid, e.gid)
    assert sorted(e.viewable_by_read) == sorted(expected["read"])
    assert sorted(e.viewable_by_write) == sorted(expected["write"])
    assert sorted(e.viewable_by_delete) == sorted(expected["delete"])

    # Owner uid is in read (POSIX user_obj rwx → owner has read).
    assert "posix:uid:1000" in e.viewable_by_read
    # The explicit ACL entry for uid 1234 is also in read.
    assert "posix:uid:1234" in e.viewable_by_read


@pytest.mark.asyncio
async def test_ingest_updates_viewable_columns_on_acl_change(
    client: AsyncClient, db_session: AsyncSession,
):
    """When the ACL changes on a re-ingested entry, the columns must
    track. A stale set would mean the next user search picks up old
    permissions — exactly the drift this phase is meant to prevent."""
    user = User(id=uuid.uuid4(), username="upd", email="u@e", password_hash="x", role="admin")
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add_all([user, source])
    await db_session.commit()
    token = create_access_token({"sub": str(user.id)})

    base_payload = {
        "source_id": str(source.id),
        "scan_id": str(uuid.uuid4()),
        "is_final": False,
        "entries": [{
            "path": "/tmp/x", "name": "x", "kind": "file",
            "mode": 0o600, "uid": 1000, "gid": 100,
        }],
    }
    resp = await client.post(
        "/api/ingest/batch", json=base_payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    # Initially: 0o600 → owner read/write only.
    e = (await db_session.execute(
        select(Entry).where(Entry.source_id == source.id)
    )).scalar_one()
    assert "posix:uid:1000" in e.viewable_by_read
    assert "*" not in e.viewable_by_read  # nobody else

    # Re-ingest with mode 0o644 (others get read).
    new_payload = dict(base_payload)
    new_payload["scan_id"] = str(uuid.uuid4())
    new_payload["entries"] = [{
        "path": "/tmp/x", "name": "x", "kind": "file",
        "mode": 0o644, "uid": 1000, "gid": 100,
    }]
    resp = await client.post(
        "/api/ingest/batch", json=new_payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    # The test session has a cached copy of `e`; we want what's on disk
    # after the second ingest committed via FastAPI's separate session.
    await db_session.refresh(e)
    # 0o644 → "other" can read → ANYONE token gains read.
    assert "*" in e.viewable_by_read


@pytest.mark.asyncio
async def test_viewable_clause_filters_rows_by_overlap(db_session: AsyncSession):
    """`viewable_clause(tokens, right)` is a Postgres array overlap. A row
    is returned iff `viewable_by_<right> && ARRAY[<tokens>]`."""
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add(source)
    await db_session.commit()

    rows = [
        Entry(
            id=uuid.uuid4(), source_id=source.id, kind="file",
            parent_path="/", path=f"/r{i}", name=f"r{i}",
            viewable_by_read=read_tokens,
            viewable_by_write=[],
            viewable_by_delete=[],
        )
        for i, read_tokens in enumerate([
            ["posix:uid:1000"],
            ["posix:uid:1001", "posix:gid:100"],
            ["sid:S-1-5-21-X"],
            ["*"],
            [],
        ])
    ]
    db_session.add_all(rows)
    await db_session.commit()

    async def visible_paths(tokens):
        result = await db_session.execute(
            select(Entry.path).where(viewable_clause(tokens, "read"))
        )
        return sorted(result.scalars().all())

    # User with just uid:1000 sees only their owned file.
    assert await visible_paths(["posix:uid:1000"]) == ["/r0"]

    # User in gid:100 sees the gid-tagged file too.
    assert await visible_paths(["posix:uid:1001", "posix:gid:100"]) == ["/r1"]

    # Anyone with the wildcard token sees the world-readable row.
    assert await visible_paths(["*"]) == ["/r3"]

    # Multi-token caller — overlap is a union, not an intersection.
    assert await visible_paths(["posix:uid:1000", "*"]) == ["/r0", "/r3"]

    # Empty token list → false() → no rows.
    assert await visible_paths([]) == []


@pytest.mark.asyncio
async def test_compute_viewable_buckets_accepts_dict_or_pydantic(db_session: AsyncSession):
    """`compute_viewable_buckets` is the funnel used by both sinks; both
    raw JSONB and typed Pydantic ACLs must produce identical output."""
    raw = {
        "type": "posix",
        "entries": [{"tag": "user", "qualifier": "42", "perms": "rwx"}],
        "default_entries": None,
    }
    via_dict = compute_viewable_buckets(raw, mode=0o600, uid=1000, gid=100)

    from akashic.schemas.acl import PosixACL
    typed = PosixACL.model_validate(raw)
    via_typed = compute_viewable_buckets(typed, mode=0o600, uid=1000, gid=100)

    assert sorted(via_dict["read"]) == sorted(via_typed["read"])
    assert "posix:uid:42" in via_dict["read"]
