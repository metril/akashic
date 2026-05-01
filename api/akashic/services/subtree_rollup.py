"""Pre-compute subtree_size_bytes / subtree_file_count / subtree_dir_count
for every directory in a source.

Recursive CTEs in Postgres can't reference aggregates in their
recursive term, so the natural "for each directory, sum descendants"
shape is awkward to express directly. Two clean alternatives:

1. Bottom-up by depth: count slashes in `path`, process deepest first,
   accumulate up. This is what we do — one UPDATE per depth level,
   monotonic state, O(N) total work.

2. Recursive CTE that walks each directory's descendants and SUMs
   them. Quadratic in deeply-nested trees and harder to reason about.

Choice (1) trades a touch of round-trip latency (one UPDATE per depth
level — typically <20 levels in real filesystems) for correctness and
explainability.

The function is invoked twice:

- After every scan completion in scan_runner — the natural end-of-scan
  step (alongside acl_denorm and snapshot_writer). All directories on
  the source get refreshed; on incremental scans this is a small cost
  because most directories' aggregates didn't actually change.

- Via tools/backfill_subtree_sizes for existing data once the migration
  has shipped but no scan has run yet.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.entry import Entry

logger = logging.getLogger(__name__)


async def rollup_source(db: AsyncSession, source_id: uuid.UUID) -> int:
    """Recompute subtree aggregates for every directory in `source_id`.

    Returns the number of directory rows updated. Caller is responsible
    for committing the session.

    Strategy:
        Step 1. For every directory at the deepest level (no
                child directories), set its aggregates from the SUM of
                its child files.
        Step 2. Walk depths from `max_depth-1` down to root,
                aggregating from the level below: a directory's totals
                are sum of child files PLUS sum of child directories'
                already-rolled-up totals.

    Postgres cost: one UPDATE per depth level. The composite index
    `(source_id, parent_path, ...)` is what makes each step's join
    cheap.
    """
    # Pre-flight: figure out the deepest directory in the source by
    # counting slashes in `path`. `length(path) - length(replace(path,'/',''))`
    # is the standard "count slashes" trick — avoids per-row Python.
    max_depth_row = (await db.execute(
        select(
            func.max(func.length(Entry.path) - func.length(func.replace(Entry.path, "/", "")))
        ).where(
            Entry.source_id == source_id,
            Entry.kind == "directory",
            Entry.is_deleted == False,  # noqa: E712
        )
    )).scalar()
    if max_depth_row is None:
        return 0
    max_depth = int(max_depth_row)

    total_updated = 0
    for depth in range(max_depth, -1, -1):
        # At each depth: aggregate the level below into me. The query
        # is the same shape every iteration, just with a different
        # depth filter.
        #
        # We use raw SQL via text() because mixing the slash-count
        # expression with SQLAlchemy ORM updates correlated to a
        # subquery is more verbose than helpful.
        update_sql = text(
            """
            UPDATE entries AS e
            SET subtree_size_bytes = COALESCE(child_files.bytes, 0)
                                     + COALESCE(child_dirs.bytes, 0),
                subtree_file_count = COALESCE(child_files.n, 0)
                                     + COALESCE(child_dirs.files, 0),
                subtree_dir_count = COALESCE(child_dirs.n, 0)
                                    + COALESCE(child_dirs.dirs, 0)
            FROM (
                SELECT id FROM entries
                WHERE source_id = :source_id
                  AND kind = 'directory'
                  AND is_deleted = false
                  AND length(path) - length(replace(path, '/', '')) = :depth
            ) AS me
            LEFT JOIN LATERAL (
                SELECT SUM(size_bytes) AS bytes, COUNT(*) AS n
                FROM entries c
                WHERE c.source_id = :source_id
                  AND c.parent_path = (SELECT path FROM entries WHERE id = me.id)
                  AND c.kind = 'file'
                  AND c.is_deleted = false
            ) AS child_files ON TRUE
            LEFT JOIN LATERAL (
                SELECT
                    SUM(c.subtree_size_bytes) AS bytes,
                    SUM(c.subtree_file_count) AS files,
                    SUM(c.subtree_dir_count) AS dirs,
                    COUNT(*) AS n
                FROM entries c
                WHERE c.source_id = :source_id
                  AND c.parent_path = (SELECT path FROM entries WHERE id = me.id)
                  AND c.kind = 'directory'
                  AND c.is_deleted = false
            ) AS child_dirs ON TRUE
            WHERE e.id = me.id
            """
        )
        result = await db.execute(
            update_sql, {"source_id": source_id, "depth": depth},
        )
        total_updated += result.rowcount or 0

    logger.info(
        "subtree_rollup: source_id=%s updated %d directory rows across %d depth levels",
        source_id, total_updated, max_depth + 1,
    )
    return total_updated
