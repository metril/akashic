"""Mint a scanner claim token directly from the running api process.

Used by `make bootstrap-scanner` so a fresh checkout can stand up an
agent through the v0.3.0 self-registration flow without going through
the UI. Prints the resulting `{token, label, pool, expires_at}` as
JSON; the caller is responsible for handing the token to a scanner
that POSTs `/api/scanners/claim`.

The keypair is NOT generated here — that's the scanner's job (the
whole point of v0.3.0's self-registration is that the private key
never leaves the scanner host).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from akashic.config import settings
from akashic.services.scanner_claim import mint_token


async def _mint(label: str, pool: str, ttl_minutes: int) -> dict:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )
    try:
        async with session_factory() as db:
            plain, row = await mint_token(
                db=db,
                label=label,
                pool=pool,
                ttl_minutes=ttl_minutes,
                # No real user; the row's created_by_user_id is nullable.
                created_by_user_id=None,
            )
            await db.commit()
            return {
                "token_id": str(row.id),
                "token": plain,
                "label": row.label,
                "pool": row.pool,
                "expires_at": row.expires_at.isoformat(),
            }
    finally:
        await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--label", required=True)
    p.add_argument("--pool", default="default")
    p.add_argument("--ttl-minutes", type=int, default=15)
    args = p.parse_args()
    out = asyncio.run(_mint(args.label, args.pool, args.ttl_minutes))
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
