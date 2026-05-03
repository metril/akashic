"""external-drive awareness: is_removable + reachability fields

Adds four columns to `sources`:

  is_removable                 — user-set hint: source is intentionally
                                 intermittent (USB, network share whose
                                 host may be offline). Default false.
  is_reachable                 — last-known reachability. NULL = never
                                 checked. true/false from the most
                                 recent /check-reachability or scan.
  last_reachable_at            — when the source was last successfully
                                 reached (any check or scan).
  last_reachability_check_at   — when the most recent reachability
                                 check ran (success OR failure).

We deliberately do NOT persist a `last_reachability_error` column —
the error is returned to the /check-reachability caller in the
response payload. Storing it long-term means a stale "old error"
sticks around forever once the drive is reconnected; the fresh
state (is_reachable=true, new timestamps) is enough.

Revision ID: 0022_source_reachability
Revises: 0021_entries_active_chash
Create Date: 2026-05-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0022_source_reachability"
down_revision: Union[str, None] = "0021_entries_active_chash"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "is_removable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "sources",
        sa.Column("is_reachable", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column(
            "last_reachable_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "last_reachability_check_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("sources", "last_reachability_check_at")
    op.drop_column("sources", "last_reachable_at")
    op.drop_column("sources", "is_reachable")
    op.drop_column("sources", "is_removable")
