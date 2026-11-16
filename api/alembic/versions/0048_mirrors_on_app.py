"""mirror config moves from backing sources (mirror_of) to app sources (mirrors)

Data-only migration.

Revision ID: 0048_mirrors_on_app
Revises: 0047_mirror_links
Create Date: 2026-11-16
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0048_mirrors_on_app"
down_revision: Union[str, None] = "0047_mirror_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_APP_TYPES = {"immich", "paperless"}
_SET = "UPDATE sources SET connection_config = CAST(:c AS jsonb) WHERE id = :id"


def _load(conn) -> dict:
    rows = conn.execute(
        sa.text("SELECT id, name, type, connection_config FROM sources ORDER BY name, id")
    ).all()
    return {
        str(r[0]): {"name": r[1], "type": r[2], "cfg": dict(r[3] or {})} for r in rows
    }


def _move(conn, src_key: str, dst_key: str, dst_ok) -> None:
    """Move list `src_key` entries onto the sources they reference under `dst_key`."""
    srcs = _load(conn)
    touched: set[str] = set()
    for sid, src in srcs.items():  # name, id order
        items = src["cfg"].get(src_key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("source_id"))
            dst = srcs.get(tid)
            if dst is None or not dst_ok(dst):
                continue
            moved = {
                "source_id": sid,
                "remote_prefix": item.get("remote_prefix", ""),
                "local_prefix": item.get("local_prefix", ""),
            }
            cur = dst["cfg"].get(dst_key)
            cur = cur if isinstance(cur, list) else []
            if moved not in cur:
                dst["cfg"][dst_key] = [*cur, moved]
                touched.add(tid)
    for sid, src in srcs.items():
        had = src_key in src["cfg"]
        src["cfg"].pop(src_key, None)
        if had or sid in touched:
            conn.execute(sa.text(_SET), {"c": json.dumps(src["cfg"]), "id": sid})


def upgrade() -> None:
    _move(op.get_bind(), "mirror_of", "mirrors", lambda s: s["type"] in _APP_TYPES)


def downgrade() -> None:
    _move(op.get_bind(), "mirrors", "mirror_of", lambda s: s["type"] not in _APP_TYPES)
