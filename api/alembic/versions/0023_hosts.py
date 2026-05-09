"""hosts table + 1:1 backfill from existing sources

Splits the `Source.connection_config` blob into a host-level row
(`hosts`) and a share-level row (`sources`). Existing non-local
sources backfill 1:1 — one host per source, named after the source —
and host-shaped keys move from `sources.connection_config` into
`hosts.connection_config`. Local sources untouched.

Auto-deduping during backfill is risky (two SMB sources with the
same host but different per-share creds shouldn't collapse), so this
preserves the exact previous behaviour. The user can later merge
duplicates via the UI.

Revision ID: 0023_hosts
Revises: 0022_source_reachability
Create Date: 2026-05-04
"""
from typing import Sequence, Union
import uuid as _uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "0023_hosts"
down_revision: Union[str, None] = "0022_source_reachability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Host-shaped keys per source type. Anything in this set moves from
# `sources.connection_config` to the new `hosts.connection_config`.
# Anything outside it stays on the source row.
#
# `ssh` was removed as a source type in v0.23.0; this branch only
# fires for sources in pre-v0.23 databases being migrated forward
# (a v0.22.x → v0.24.0+ upgrade still needs to split SSH host-keys
# correctly so the resulting `hosts` row is well-formed even though
# no live code path will ever scan that host afterward).
_HOST_KEYS_BY_TYPE: dict[str, frozenset[str]] = {
    "ssh": frozenset({
        "host", "port", "username", "password",
        "key_path", "key_passphrase", "known_hosts_path",
    }),
    "smb": frozenset({
        "host", "port", "username", "password", "domain",
    }),
    "nfs": frozenset({
        "host", "port",
        "auth_method", "auth_uid", "auth_gid", "auth_aux_gids",
        "krb5_principal", "krb5_realm", "krb5_service_principal",
        "krb5_keytab_path", "krb5_password", "krb5_config_path",
    }),
    "s3": frozenset({
        "endpoint", "region", "access_key_id", "secret_access_key",
    }),
}


def upgrade() -> None:
    # --- Schema -------------------------------------------------------
    op.create_table(
        "hosts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column(
            "connection_config",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
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
            nullable=False,
        ),
        sa.UniqueConstraint("name", name="uq_hosts_name"),
    )

    op.add_column(
        "sources",
        sa.Column("host_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_sources_host_id_hosts",
        "sources", "hosts",
        ["host_id"], ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_sources_host_id", "sources", ["host_id"])

    # --- Backfill -----------------------------------------------------
    # 1:1 host per non-local source. Move host-shaped keys into the new
    # host row; leave share-shaped keys on the source row.
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, name, type, connection_config FROM sources "
        "WHERE type <> 'local'"
    )).fetchall()

    # If two sources have the same name (shouldn't — sources.name is
    # unique — but be defensive), suffix the host name to avoid the
    # uq_hosts_name collision.
    used_host_names: set[str] = set()

    for row in rows:
        source_id, source_name, source_type, cfg = row
        cfg = dict(cfg or {})
        host_keys = _HOST_KEYS_BY_TYPE.get(source_type, frozenset())
        host_cfg = {k: v for k, v in cfg.items() if k in host_keys}
        share_cfg = {k: v for k, v in cfg.items() if k not in host_keys}

        host_name = source_name
        n = 2
        while host_name in used_host_names:
            host_name = f"{source_name} ({n})"
            n += 1
        used_host_names.add(host_name)

        host_id = _uuid.uuid4()
        bind.execute(
            sa.text(
                "INSERT INTO hosts (id, name, type, connection_config) "
                "VALUES (:id, :name, :type, CAST(:cfg AS jsonb))"
            ),
            {
                "id": host_id,
                "name": host_name,
                "type": source_type,
                "cfg": _json(host_cfg),
            },
        )
        bind.execute(
            sa.text(
                "UPDATE sources "
                "SET host_id = :host_id, connection_config = CAST(:cfg AS jsonb) "
                "WHERE id = :id"
            ),
            {"host_id": host_id, "cfg": _json(share_cfg), "id": source_id},
        )


def _json(d: dict) -> str:
    import json
    return json.dumps(d)


def downgrade() -> None:
    # Best-effort: re-merge host config back into source rows so the
    # legacy code path (sources owning the full connection_config)
    # works after the column drop. Local sources are already correct.
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT s.id, s.connection_config, h.connection_config "
        "FROM sources s JOIN hosts h ON s.host_id = h.id"
    )).fetchall()
    for source_id, source_cfg, host_cfg in rows:
        merged = dict(host_cfg or {})
        merged.update(dict(source_cfg or {}))
        bind.execute(
            sa.text(
                "UPDATE sources SET connection_config = CAST(:cfg AS jsonb) "
                "WHERE id = :id"
            ),
            {"cfg": _json(merged), "id": source_id},
        )

    op.drop_index("ix_sources_host_id", table_name="sources")
    op.drop_constraint("fk_sources_host_id_hosts", "sources", type_="foreignkey")
    op.drop_column("sources", "host_id")
    op.drop_table("hosts")
