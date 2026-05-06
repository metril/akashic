"""OAuth foundation: oauth_app_configs + source_oauth_credentials

v0.12.0 — Tier 1 PR-A.

Two tables:

oauth_app_configs (per-provider OAuth client app, set by deployment owner)
  provider PK (e.g. "google", "microsoft")
  client_id text
  client_secret_encrypted text  (Fernet, key derived from AKASHIC_SECRET_KEY)
  redirect_uri text             (the URL registered with the provider; the
                                 callback route normalizes against this)
  configured_by_user_id FK users
  configured_at, updated_at

source_oauth_credentials (per-source refresh-token grant)
  id uuid pk
  source_id FK sources (nullable — see below)
  provider text
  refresh_token_encrypted text
  access_token_cached text nullable
  access_token_expires_at timestamptz nullable
  scope text nullable
  account_email text nullable      (for UI display: "connected as alice@…")
  account_label text nullable      (provider's display name when available)
  created_at, updated_at

source_id is nullable because PR-A's smoke-test path creates a credential
that isn't yet attached to a source row — Tier-1 PR-C ties the
"sign in with Google" step in the AddSourceForm to an actual source. A
unique partial index keeps source_id one-to-one when set.

Revision ID: 0029_oauth_foundation
Revises: 0028_entries_domain_metadata
Create Date: 2026-05-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0029_oauth_foundation"
down_revision: Union[str, None] = "0028_entries_domain_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oauth_app_configs",
        sa.Column("provider", sa.String(), primary_key=True),
        sa.Column("client_id", sa.String(), nullable=False),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.String(), nullable=False),
        sa.Column(
            "configured_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "configured_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "source_oauth_credentials",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "source_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=False),
        sa.Column("access_token_cached", sa.Text(), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("account_email", sa.String(), nullable=True),
        sa.Column("account_label", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    # Each source can have at most one OAuth grant. Partial unique so
    # smoke-test rows (source_id NULL) stay legal.
    op.create_index(
        "ux_source_oauth_credentials_source",
        "source_oauth_credentials",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("source_id IS NOT NULL"),
    )
    op.create_index(
        "ix_source_oauth_credentials_provider",
        "source_oauth_credentials",
        ["provider"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_source_oauth_credentials_provider",
        table_name="source_oauth_credentials",
    )
    op.drop_index(
        "ux_source_oauth_credentials_source",
        table_name="source_oauth_credentials",
    )
    op.drop_table("source_oauth_credentials")
    op.drop_table("oauth_app_configs")
