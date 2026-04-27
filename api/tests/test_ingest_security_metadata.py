import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.source import Source
from akashic.models.user import User
from akashic.auth.jwt import create_access_token


@pytest.mark.asyncio
async def test_ingest_persists_source_security_metadata(
    client: AsyncClient, db_session: AsyncSession
):
    user = User(
        id=uuid.uuid4(), username="admin", email="a@b.c",
        password_hash="x", role="admin",
    )
    source = Source(
        id=uuid.uuid4(), name="b", type="s3",
        connection_config={"bucket": "x"},
    )
    db_session.add_all([user, source])
    await db_session.commit()

    token = create_access_token({"sub": str(user.id)})
    payload = {
        "source_id": str(source.id),
        "scan_id": str(uuid.uuid4()),
        "entries": [],
        "is_final": False,
        "source_security_metadata": {
            "captured_at": "2026-04-26T00:00:00Z",
            "is_public_inferred": True,
        },
    }
    resp = await client.post(
        "/api/ingest/batch", json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    result = await db_session.execute(select(Source).where(Source.id == source.id))
    fetched = result.scalar_one()
    assert fetched.security_metadata["is_public_inferred"] is True
