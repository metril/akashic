import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from akashic.models.source import Source


@pytest.mark.asyncio
async def test_source_can_persist_security_metadata(db_session: AsyncSession):
    src = Source(
        id=uuid.uuid4(),
        name="bucket-test",
        type="s3",
        connection_config={"bucket": "x"},
        security_metadata={
            "captured_at": "2026-04-26T00:00:00Z",
            "is_public_inferred": False,
            "public_access_block": {
                "block_public_acls": True,
                "ignore_public_acls": True,
                "block_public_policy": True,
                "restrict_public_buckets": True,
            },
        },
    )
    db_session.add(src)
    await db_session.commit()

    result = await db_session.execute(select(Source).where(Source.id == src.id))
    fetched = result.scalar_one()
    assert fetched.security_metadata["is_public_inferred"] is False
    assert fetched.security_metadata["public_access_block"]["block_public_acls"] is True
