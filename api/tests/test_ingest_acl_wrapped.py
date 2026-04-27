import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.auth.jwt import create_access_token


@pytest.mark.asyncio
async def test_wrapped_posix_acl_round_trips(client: AsyncClient, db_session: AsyncSession):
    user = User(id=uuid.uuid4(), username="u", email="u@e", password_hash="x", role="admin")
    source = Source(id=uuid.uuid4(), name="s", type="local", connection_config={})
    db_session.add_all([user, source])
    await db_session.commit()
    token = create_access_token({"sub": str(user.id)})

    payload = {
        "source_id": str(source.id),
        "scan_id": str(uuid.uuid4()),
        "entries": [{
            "path": "/tmp/foo", "name": "foo", "kind": "file",
            "acl": {
                "type": "posix",
                "entries": [{"tag": "user", "qualifier": "alice", "perms": "rwx"}],
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

    result = await db_session.execute(select(Entry).where(Entry.source_id == source.id))
    e = result.scalar_one()
    assert e.acl["type"] == "posix"
    assert e.acl["entries"][0]["qualifier"] == "alice"
