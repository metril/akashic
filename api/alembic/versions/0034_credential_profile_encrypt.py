"""credential_profiles.credentials_encrypted — at-rest encryption

v0.29.5 — the `credentials` JSONB column on `credential_profiles`
stores plaintext usernames + passwords today. Anyone with DB read
access sees every SMB/NFS credential. This migration:

  1. Adds a sibling `credentials_encrypted: bytea NULL` column.
  2. Iterates every existing row, encrypts the plaintext dict via
     :func:`akashic.services.credential_crypto.encrypt_credentials`,
     stores the ciphertext in the new column, NULLs the plaintext
     column.
  3. Refuses to run on a deployment that hasn't set a real SECRET_KEY
     (the same gate the config validator uses) — encrypting with the
     dev default would offer zero protection.

Downgrade decrypts in-place and restores plaintext into `credentials`,
then drops the encrypted column. Reversible, no data loss assuming
the same SECRET_KEY is in effect.

Idempotent on re-run: rows that already have a non-NULL
`credentials_encrypted` are skipped.

Revision ID: 0034_credential_profile_encrypt
Revises: 0033_scan_curr_batch_size
Create Date: 2026-05-15
"""
from __future__ import annotations

import json
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0034_cred_prof_encrypt"
down_revision: Union[str, None] = "0033_scan_curr_batch_size"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DEV_SECRET_KEY = "changeme-secret-key"


def _assert_real_secret_key() -> None:
    """Fail the migration if SECRET_KEY is the shipped default.

    Encrypting with a known key offers zero protection — better to
    refuse than to ship a deployment that thinks its credentials are
    encrypted. The opt-out is AKASHIC_DEV_ALLOW_DEFAULT_KEY=1 (same
    gate the config validator uses) for the test suite.
    """
    key = os.environ.get("SECRET_KEY", _DEV_SECRET_KEY)
    if key == _DEV_SECRET_KEY and not os.environ.get(
        "AKASHIC_DEV_ALLOW_DEFAULT_KEY"
    ):
        raise RuntimeError(
            "Migration 0034 refuses to run with the shipped default "
            "SECRET_KEY — encrypted credentials would offer no real "
            "protection. Set SECRET_KEY to a long random value first."
        )


def upgrade() -> None:
    _assert_real_secret_key()

    op.add_column(
        "credential_profiles",
        sa.Column("credentials_encrypted", sa.LargeBinary(), nullable=True),
    )
    # Make the plaintext column nullable now that the encrypted column
    # carries the load. Existing rows still have plaintext until the
    # sweep below clears it.
    op.alter_column(
        "credential_profiles", "credentials",
        existing_type=sa.dialects.postgresql.JSONB(),
        nullable=True,
    )

    from akashic.services.credential_crypto import encrypt_credentials

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, credentials FROM credential_profiles "
        "WHERE credentials_encrypted IS NULL "
        "  AND credentials IS NOT NULL"
    )).fetchall()
    for row in rows:
        # asyncpg returns dict directly for JSONB; psycopg2 returns
        # the raw json string. Normalise.
        creds = row.credentials
        if isinstance(creds, str):
            creds = json.loads(creds)
        ct = encrypt_credentials(creds or {})
        bind.execute(
            sa.text(
                "UPDATE credential_profiles "
                "SET credentials_encrypted = :ct, credentials = NULL "
                "WHERE id = :id"
            ),
            {"ct": ct, "id": row.id},
        )


def downgrade() -> None:
    from akashic.services.credential_crypto import decrypt_credentials

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, credentials_encrypted FROM credential_profiles "
        "WHERE credentials_encrypted IS NOT NULL"
    )).fetchall()
    for row in rows:
        plain = decrypt_credentials(bytes(row.credentials_encrypted))
        bind.execute(
            sa.text(
                "UPDATE credential_profiles "
                "SET credentials = CAST(:plain AS jsonb), "
                "    credentials_encrypted = NULL "
                "WHERE id = :id"
            ),
            {"plain": json.dumps(plain), "id": row.id},
        )

    op.drop_column("credential_profiles", "credentials_encrypted")
    op.alter_column(
        "credential_profiles", "credentials",
        existing_type=sa.dialects.postgresql.JSONB(),
        nullable=False,
    )
