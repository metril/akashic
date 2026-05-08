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


async def rollup_source(
    db: AsyncSession,
    source_id: uuid.UUID,
    *,
    null_only: bool = False,
) -> int:
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

    When `null_only=True` (Phase B safety-net mode), only rows whose
    `subtree_size_bytes IS NULL` are updated — used by the post-scan
    background task so connectors that emit subtree totals at scan
    time aren't clobbered by the API. The full-recompute path
    (backfill tool, manual admin re-rollup) keeps `null_only=False`.
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
        # null_only kicks in here — we add a filter to the `me` subquery
        # so only NULL rows enter the update set. Children that were
        # scanner-populated still aggregate up correctly because the
        # LATERAL queries read their actual values regardless.
        # Inline path into the `me` subquery (review D-I7) so the
        # LATERAL joins compare against a column in the same scope
        # rather than re-fetching with a correlated `(SELECT path
        # FROM entries WHERE id = me.id)` per row. Saves one
        # nested-loop key lookup per directory at every depth level.
        update_sql = text(
            f"""
            UPDATE entries AS e
            SET subtree_size_bytes = COALESCE(child_files.bytes, 0)
                                     + COALESCE(child_dirs.bytes, 0),
                subtree_file_count = COALESCE(child_files.n, 0)
                                     + COALESCE(child_dirs.files, 0),
                subtree_dir_count = COALESCE(child_dirs.n, 0)
                                    + COALESCE(child_dirs.dirs, 0)
            FROM (
                SELECT id, path FROM entries inner_e
                WHERE inner_e.source_id = :source_id
                  AND inner_e.kind = 'directory'
                  AND inner_e.is_deleted = false
                  AND length(inner_e.path) - length(replace(inner_e.path, '/', '')) = :depth
                  {("AND inner_e.subtree_size_bytes IS NULL" if null_only else "")}
            ) AS me
            LEFT JOIN LATERAL (
                SELECT SUM(size_bytes) AS bytes, COUNT(*) AS n
                FROM entries c
                WHERE c.source_id = :source_id
                  AND c.parent_path = me.path
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
                  AND c.parent_path = me.path
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


# v0.4.11 Phase 8 — per-directory top-K children, computed at scan
# time so the storage tree read path is "iterative expansion of
# indexed JSONB" instead of "recursive CTE." K=256 is generous; the
# WebGL renderer culls anything past the first ~50-200 visible rects
# at any given depth anyway, but storing more gives Phase 9's
# wheel-zoom headroom.
TOP_CHILDREN_K = 256


async def rollup_top_children(
    db: AsyncSession,
    source_id: uuid.UUID,
) -> int:
    """Compute and store `top_children` JSONB for every directory in
    `source_id`. Returns the number of directory rows updated.

    Strategy: one big SQL UPDATE that builds the JSONB per row from a
    LATERAL subquery over each directory's children. Cheaper than
    loading every directory into Python and writing back row-by-row,
    and the planner can use ix_entries_tree_walk (Phase 6) to do the
    LATERAL ORDER BY ... LIMIT :k as an Index Scan.

    Caller commits the session.
    """
    # Build the JSONB inline. The structure mirrors what
    # storage_explorer._fetch_tree_rows_v2 expects to read.
    update_sql = text(
        f"""
        UPDATE entries AS d
        SET top_children = sub.tc
        FROM (
            SELECT
                me.id AS dir_id,
                jsonb_build_object(
                    'k', :k,
                    'computed_at', now() AT TIME ZONE 'utc',
                    'children', COALESCE(top.children_json, '[]'::jsonb),
                    'other_size', COALESCE(rest.other_total, 0)
                ) AS tc
            FROM entries me
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'id', c.id::text,
                        'name', c.name,
                        'path', c.path,
                        'kind', c.kind,
                        'size', COALESCE(c.subtree_size_bytes, c.size_bytes, 0),
                        'color_key', c.extension,
                        'owner', c.owner_name,
                        'modified', c.fs_modified_at
                    )
                    ORDER BY COALESCE(c.subtree_size_bytes, c.size_bytes, 0) DESC,
                             c.id
                ) AS children_json
                FROM (
                    SELECT id, name, path, kind, size_bytes,
                           subtree_size_bytes, extension, owner_name,
                           fs_modified_at
                    FROM entries
                    WHERE source_id = :source_id
                      AND parent_path = me.path
                      AND is_deleted = false
                    ORDER BY COALESCE(subtree_size_bytes, size_bytes, 0) DESC,
                             id
                    LIMIT :k
                ) c
            ) top ON TRUE
            LEFT JOIN LATERAL (
                -- Sum the effective sizes of the children we cut
                -- (rank > k). Drives the synthetic <other> rect in
                -- the visualizer.
                SELECT SUM(COALESCE(subtree_size_bytes, size_bytes, 0)) AS other_total
                FROM (
                    SELECT subtree_size_bytes, size_bytes
                    FROM entries
                    WHERE source_id = :source_id
                      AND parent_path = me.path
                      AND is_deleted = false
                    ORDER BY COALESCE(subtree_size_bytes, size_bytes, 0) DESC,
                             id
                    OFFSET :k
                ) skipped
            ) rest ON TRUE
            WHERE me.source_id = :source_id
              AND me.kind = 'directory'
              AND me.is_deleted = false
        ) sub
        WHERE d.id = sub.dir_id
        """
    )
    result = await db.execute(
        update_sql,
        {"source_id": source_id, "k": TOP_CHILDREN_K},
    )
    updated = result.rowcount or 0
    logger.info(
        "top_children_rollup: source_id=%s updated %d directories (K=%d)",
        source_id, updated, TOP_CHILDREN_K,
    )
    return updated


async def rollup_top_children_for_paths(
    db: AsyncSession,
    source_id: uuid.UUID,
    paths: list[str],
) -> int:
    """Recompute `top_children` JSONB for a specific set of directory
    paths (incremental update after an ingest batch touched them).
    Same SQL shape as `rollup_top_children` but with an extra
    parent-path filter on the outer `me` selection.

    Used by the streaming worker to avoid recomputing every directory
    on every ingest batch — only the parents whose contents actually
    changed get refreshed.
    """
    if not paths:
        return 0

    update_sql = text(
        f"""
        UPDATE entries AS d
        SET top_children = sub.tc
        FROM (
            SELECT
                me.id AS dir_id,
                jsonb_build_object(
                    'k', :k,
                    'computed_at', now() AT TIME ZONE 'utc',
                    'children', COALESCE(top.children_json, '[]'::jsonb),
                    'other_size', COALESCE(rest.other_total, 0)
                ) AS tc
            FROM entries me
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'id', c.id::text,
                        'name', c.name,
                        'path', c.path,
                        'kind', c.kind,
                        'size', COALESCE(c.subtree_size_bytes, c.size_bytes, 0),
                        'color_key', c.extension,
                        'owner', c.owner_name,
                        'modified', c.fs_modified_at
                    )
                    ORDER BY COALESCE(c.subtree_size_bytes, c.size_bytes, 0) DESC,
                             c.id
                ) AS children_json
                FROM (
                    SELECT id, name, path, kind, size_bytes,
                           subtree_size_bytes, extension, owner_name,
                           fs_modified_at
                    FROM entries
                    WHERE source_id = :source_id
                      AND parent_path = me.path
                      AND is_deleted = false
                    ORDER BY COALESCE(subtree_size_bytes, size_bytes, 0) DESC,
                             id
                    LIMIT :k
                ) c
            ) top ON TRUE
            LEFT JOIN LATERAL (
                SELECT SUM(COALESCE(subtree_size_bytes, size_bytes, 0)) AS other_total
                FROM (
                    SELECT subtree_size_bytes, size_bytes
                    FROM entries
                    WHERE source_id = :source_id
                      AND parent_path = me.path
                      AND is_deleted = false
                    ORDER BY COALESCE(subtree_size_bytes, size_bytes, 0) DESC,
                             id
                    OFFSET :k
                ) skipped
            ) rest ON TRUE
            WHERE me.source_id = :source_id
              AND me.kind = 'directory'
              AND me.is_deleted = false
              AND me.path = ANY(:paths)
        ) sub
        WHERE d.id = sub.dir_id
        """
    )
    result = await db.execute(
        update_sql,
        {"source_id": source_id, "k": TOP_CHILDREN_K, "paths": paths},
    )
    return result.rowcount or 0
