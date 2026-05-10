"""reachability redesign — drop continuous-poll queue + cached source/host
fields; add append-only reachability_results history table.

Three things in one revision (the user's design rebuild is one logical
unit, so a partial-state head would be incoherent):

  1. DROP `reachability_checks` — the queue+results conflation that the
     deleted scheduler loops fed.
  2. DROP `sources.is_reachable / last_reachability_check_at /
     last_reachable_at` and the same three on `hosts`. The cached
     per-source flag was always derived; the new model derives state on
     read from the latest result row across all scanners (and from the
     latest successful scan, which is the strongest possible probe).
  3. CREATE `reachability_results` — append-only, last-N-per-pair
     retained by a daily prune. `scanner_id IS NULL` represents an
     inline test the API ran itself (non-local sources can be probed
     directly — no agent round-trip needed).

Irreversible: the dropped data was ephemeral status (last few minutes
of probe results), and reconstructing the old `reachability_checks`
queue from the new history is not meaningful work. `downgrade()`
recreates the empty tables/columns so a forced rollback doesn't break
SQLAlchemy reflection, but the deleted data is gone.

Revision ID: 0031_reachability_ondemand
Revises: 0030_entries_native_id
Create Date: 2026-05-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "0031_reachability_ondemand"
down_revision: Union[str, None] = "0030_entries_native_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Drop the queue+results table from the old continuous-poll model.
    op.drop_index(
        "uq_reachability_checks_pending", table_name="reachability_checks",
    )
    op.drop_index(
        "ix_reachability_checks_lease", table_name="reachability_checks",
    )
    op.drop_index(
        "ix_reachability_checks_source_id", table_name="reachability_checks",
    )
    op.drop_table("reachability_checks")

    # 2. Drop the cached reachability columns on sources + hosts. State is
    #    now derived on read from `reachability_results` + scan completion.
    op.drop_column("sources", "last_reachability_check_at")
    op.drop_column("sources", "last_reachable_at")
    op.drop_column("sources", "is_reachable")
    op.drop_column("hosts", "last_reachability_check_at")
    op.drop_column("hosts", "last_reachable_at")
    op.drop_column("hosts", "is_reachable")

    # 3. Create the append-only result history.
    op.create_table(
        "reachability_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id", UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # NULL = inline probe by the API itself (non-local sources). The
        # API can dial SMB/NFS/S3/etc. directly without an agent round-
        # trip; that result is recorded against the source with no
        # scanner attribution.
        sa.Column(
            "scanner_id", UUID(as_uuid=True),
            sa.ForeignKey("scanners.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("step", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        # NULL when the result was bumped implicitly by a scan completion
        # (no specific user clicked anything); set for explicit /test-…
        # POSTs so the audit trail can attribute who asked.
        sa.Column(
            "triggered_by", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # The hot-path read is "latest result per (source, scanner) pair"
    # for the eligibility panels and the source/scanner reachability
    # summaries. DESC on completed_at lets `LIMIT 1` resolve via index
    # without a sort.
    op.create_index(
        "ix_reachability_results_pair_completed",
        "reachability_results",
        ["source_id", "scanner_id", sa.text("completed_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reachability_results_pair_completed",
        table_name="reachability_results",
    )
    op.drop_table("reachability_results")
    op.add_column(
        "hosts",
        sa.Column(
            "is_reachable", sa.Boolean(), nullable=True,
        ),
    )
    op.add_column(
        "hosts",
        sa.Column(
            "last_reachable_at", sa.DateTime(timezone=True), nullable=True,
        ),
    )
    op.add_column(
        "hosts",
        sa.Column(
            "last_reachability_check_at",
            sa.DateTime(timezone=True), nullable=True,
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "is_reachable", sa.Boolean(), nullable=True,
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "last_reachable_at", sa.DateTime(timezone=True), nullable=True,
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "last_reachability_check_at",
            sa.DateTime(timezone=True), nullable=True,
        ),
    )
    op.create_table(
        "reachability_checks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id", UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(),
            nullable=False, server_default="pending",
        ),
        sa.Column(
            "assigned_scanner_id", UUID(as_uuid=True),
            sa.ForeignKey("scanners.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pool", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_ok", sa.Boolean(), nullable=True),
        sa.Column("result_step", sa.String(), nullable=True),
        sa.Column("result_error", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_reachability_checks_source_id",
        "reachability_checks", ["source_id"],
    )
    op.create_index(
        "ix_reachability_checks_lease",
        "reachability_checks",
        ["status", "lease_expires_at"],
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )
    op.create_index(
        "uq_reachability_checks_pending",
        "reachability_checks",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
