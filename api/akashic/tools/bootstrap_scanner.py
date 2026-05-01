"""Mint a scanner directly from the running api process.

Used by `make bootstrap-scanner` so a fresh checkout can stand up an
agent without going through the UI. Generates an Ed25519 keypair,
inserts the scanner row, writes the private key to disk, and prints
the scanner id + public fingerprint to stdout as JSON.

The caller (the Makefile) is responsible for getting the private key
out of the api container and onto the scanner host.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from akashic.config import settings
from akashic.models.scanner import Scanner
from akashic.services.scanner_keys import generate_keypair


async def _bootstrap(name: str, pool: str, key_out: Path) -> dict:
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            existing = await db.execute(select(Scanner).where(Scanner.name == name))
            if existing.scalar_one_or_none() is not None:
                raise SystemExit(
                    f"scanner '{name}' already exists; "
                    f"delete it via the UI or pick a different name",
                )
            kp = generate_keypair()
            scanner = Scanner(
                name=name,
                pool=pool,
                public_key_pem=kp.public_pem,
                key_fingerprint=kp.fingerprint,
            )
            db.add(scanner)
            await db.commit()
            await db.refresh(scanner)
            key_out.write_text(kp.private_pem)
            key_out.chmod(0o600)
            return {
                "id": str(scanner.id),
                "name": scanner.name,
                "pool": scanner.pool,
                "key_fingerprint": kp.fingerprint,
            }
    finally:
        await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--name", required=True)
    p.add_argument("--pool", default="default")
    p.add_argument("--key-out", required=True, type=Path)
    args = p.parse_args()
    out = asyncio.run(_bootstrap(args.name, args.pool, args.key_out))
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
