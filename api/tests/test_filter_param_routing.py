"""Phase 6 — `?filters=<base64url>` accepted by Browse and Search.

The grammar itself is exercised in test_filter_grammar.py. Here we only
check the routing seam: that the routers accept the param, AND it with
their existing predicates, and reject the cross-source case for Browse
with the right hint.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import create_access_token
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services.filter_grammar import OwnerPred, SourcePred, serialize


@pytest.mark.asyncio
async def test_browse_applies_grammar_filters(client: AsyncClient, db_session: AsyncSession):
    """Browse with `filters=` containing an owner predicate should narrow
    the listing to that owner only — matching the SQL-side `to_sqlalchemy`
    output."""
    admin = User(id=uuid.uuid4(), username="adm", email="a@e", password_hash="x", role="admin")
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add_all([admin, source])
    await db_session.commit()
    db_session.add_all([
        Entry(
            id=uuid.uuid4(), source_id=source.id, kind="file",
            parent_path="/", path="/alice.txt", name="alice.txt",
            owner_name="alice",
            viewable_by_read=[], viewable_by_write=[], viewable_by_delete=[],
        ),
        Entry(
            id=uuid.uuid4(), source_id=source.id, kind="file",
            parent_path="/", path="/bob.txt", name="bob.txt",
            owner_name="bob",
            viewable_by_read=[], viewable_by_write=[], viewable_by_delete=[],
        ),
    ])
    await db_session.commit()
    token = create_access_token({"sub": str(admin.id)})

    encoded = serialize([OwnerPred(kind="owner", value="alice")])
    resp = await client.get(
        f"/api/browse?source_id={source.id}&filters={encoded}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    paths = sorted(e["path"] for e in resp.json()["entries"])
    assert paths == ["/alice.txt"]  # bob.txt is filtered out


@pytest.mark.asyncio
async def test_browse_rejects_cross_source_predicate(client: AsyncClient, db_session: AsyncSession):
    """A `source` predicate makes no sense in single-source-scoped Browse;
    the API must respond with a 400 + a hint to switch to Search rather
    than silently returning an empty listing."""
    admin = User(id=uuid.uuid4(), username="adm2", email="a2@e", password_hash="x", role="admin")
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add_all([admin, source])
    await db_session.commit()
    token = create_access_token({"sub": str(admin.id)})

    encoded = serialize([SourcePred(kind="source", value=str(uuid.uuid4()))])
    resp = await client.get(
        f"/api/browse?source_id={source.id}&filters={encoded}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Search" in detail


@pytest.mark.asyncio
async def test_browse_rejects_malformed_filters(client: AsyncClient, db_session: AsyncSession):
    """A truly invalid base64 / json payload should 400, not 500."""
    admin = User(id=uuid.uuid4(), username="adm3", email="a3@e", password_hash="x", role="admin")
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add_all([admin, source])
    await db_session.commit()
    token = create_access_token({"sub": str(admin.id)})

    resp = await client.get(
        f"/api/browse?source_id={source.id}&filters=NOT_BASE64!!!",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
