"""Manual EXPLAIN ANALYZE harness for the storage tree CTE.

Not part of CI; meant to be run by an operator pre-deploy or post-
deploy on a real install to validate that Phase 6's index +
LATERAL CTE rewrite are picked up by the planner.

Usage:
    DATABASE_URL=postgres://... python -m scripts.explain_storage_tree \
        --source-id <UUID> --path / --max-nodes 100000

Prints the plan + total time. Look for `Index Scan using
ix_entries_tree_walk` (good) — fall back to `Seq Scan` (bad,
investigate).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


SQL_TEMPLATE = """
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
WITH RECURSIVE walk AS (
    SELECT entries.id, entries.parent_path, entries.path,
           entries.name, entries.kind,
           entries.size_bytes, entries.subtree_size_bytes,
           0 AS depth
    FROM entries
    WHERE entries.source_id = :source_id
      AND entries.is_deleted = false
      AND (
          entries.path = :root_path
          OR (
              entries.parent_path = :root_path
              AND NOT EXISTS (
                  SELECT 1 FROM entries r
                   WHERE r.source_id = :source_id
                     AND r.path = :root_path
                     AND r.is_deleted = false
              )
          )
      )
    UNION ALL
    SELECT child.id, child.parent_path, child.path, child.name, child.kind,
           child.size_bytes, child.subtree_size_bytes,
           walk.depth + 1
    FROM walk
    CROSS JOIN LATERAL (
        SELECT c.id, c.parent_path, c.path, c.name, c.kind,
               c.size_bytes, c.subtree_size_bytes
        FROM entries c
        WHERE c.source_id = :source_id
          AND c.parent_path = walk.path
          AND c.is_deleted = false
          AND COALESCE(c.subtree_size_bytes, c.size_bytes, 0) >= :min_bytes
        ORDER BY COALESCE(c.subtree_size_bytes, c.size_bytes, 0) DESC, c.id
        LIMIT :per_dir_k
    ) child
    WHERE walk.kind = 'directory'
)
SELECT id, parent_path, path, name, kind,
       size_bytes, subtree_size_bytes, depth
FROM walk
ORDER BY (path = :root_path) DESC,
         COALESCE(subtree_size_bytes, size_bytes, 0) DESC
LIMIT :max_nodes
"""


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-id", required=True, type=uuid.UUID)
    ap.add_argument("--path", default="/")
    ap.add_argument("--max-nodes", type=int, default=50000)
    ap.add_argument("--per-dir-k", type=int, default=1000)
    ap.add_argument("--min-bytes", type=int, default=0)
    ap.add_argument(
        "--db-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres connection string; falls back to $DATABASE_URL.",
    )
    args = ap.parse_args()
    if not args.db_url:
        ap.error("DATABASE_URL is required (env or --db-url)")

    # asyncpg URL form
    url = args.db_url
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    elif url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://"):]

    engine = create_async_engine(url, echo=False)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(SQL_TEMPLATE),
            {
                "source_id": args.source_id,
                "root_path": args.path,
                "max_nodes": args.max_nodes,
                "min_bytes": args.min_bytes,
                "per_dir_k": args.per_dir_k,
            },
        )
        for row in result.all():
            # EXPLAIN returns one column: the plan text line.
            print(row[0])
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
