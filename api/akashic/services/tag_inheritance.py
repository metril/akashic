"""Apply / remove / propagate user-applied tags with directory inheritance.

The data model (see api/akashic/models/tag.py) materialises one
`entry_tags` row per (entry, tag, origin). A direct tag has
`inherited_from_entry_id IS NULL`; an inherited tag points at the
ancestor directory the tag was applied to.

Why materialised rather than virtual: Search filters route through
Meilisearch, where `tags` is an array attribute on each entry doc. A
virtual / path-walk model would require recomputing inheritance on
every search; materialising once at apply time keeps the read path
identical to the no-inheritance case. The cost is write amplification
on directory-apply (one big INSERT … SELECT) and on tree moves (a
handful of small DELETE/INSERT pairs per moved row); both are bounded
and run at scan or admin-action time, not on the user's read path.

Each helper below takes an `AsyncSession` and does NOT commit — the
caller batches into its own transaction so the API endpoint or the
ingest router decides commit boundaries. Affected entry IDs are
returned so the caller can enqueue a Meili re-index.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def apply_tag(
    db: AsyncSession,
    *,
    entry_id: uuid.UUID,
    tag: str,
    user_id: uuid.UUID,
) -> list[uuid.UUID]:
    """Apply `tag` to `entry_id`. If the entry is a directory, also
    materialise inherited rows on every descendant.

    Returns the list of entry IDs that were touched (direct + every
    descendant that gained an inherited row). The caller uses this for
    Meili re-indexing.
    """
    affected: list[uuid.UUID] = [entry_id]

    # 1) The direct row.
    await db.execute(
        text(
            """
            INSERT INTO entry_tags
                (id, entry_id, tag, inherited_from_entry_id, created_by_user_id)
            VALUES
                (gen_random_uuid(), :entry_id, :tag, NULL, :user_id)
            ON CONFLICT (entry_id, tag, inherited_from_entry_id) DO NOTHING
            """
        ),
        {"entry_id": entry_id, "tag": tag, "user_id": user_id},
    )

    # 2) If E is a directory, materialise inheritance.
    # The LIKE pattern is built by Postgres from the ancestor's stored
    # path; without ESCAPE handling, a path that happens to contain `%`
    # or `_` (legal on every OS) would match unrelated subtrees. The
    # `replace()` chain escapes both wildcards plus the escape char itself
    # under the explicit ESCAPE '\' clause.
    res = await db.execute(
        text(
            r"""
            INSERT INTO entry_tags
                (id, entry_id, tag, inherited_from_entry_id, created_by_user_id)
            SELECT
                gen_random_uuid(), c.id, :tag, :entry_id, :user_id
            FROM entries c
            JOIN entries anc ON anc.id = :entry_id
            WHERE c.source_id = anc.source_id
              AND anc.kind = 'directory'
              AND c.id <> anc.id
              AND c.is_deleted = false
              AND (
                    c.path = anc.path
                 OR c.path LIKE
                     replace(replace(replace(anc.path, '\', '\\'), '%', '\%'), '_', '\_')
                     || '/%' ESCAPE '\'
              )
            ON CONFLICT (entry_id, tag, inherited_from_entry_id) DO NOTHING
            RETURNING entry_id
            """
        ),
        {"entry_id": entry_id, "tag": tag, "user_id": user_id},
    )
    affected.extend(row[0] for row in res.fetchall())
    return affected


async def remove_tag(
    db: AsyncSession,
    *,
    entry_id: uuid.UUID,
    tag: str,
) -> list[uuid.UUID]:
    """Remove `tag` from `entry_id` and cascade to every inherited copy
    that descended from this origin.

    A descendant that was *also* directly tagged with the same tag
    keeps its direct row — only the inherited copies sourced from this
    entry disappear.

    Returns the list of entry IDs whose effective tag set may have
    changed (the entry itself + every inherited-row holder).
    """
    res = await db.execute(
        text(
            """
            DELETE FROM entry_tags
            WHERE
                (entry_id = :entry_id
                 AND tag = :tag
                 AND inherited_from_entry_id IS NULL)
             OR (tag = :tag AND inherited_from_entry_id = :entry_id)
            RETURNING entry_id
            """
        ),
        {"entry_id": entry_id, "tag": tag},
    )
    return [row[0] for row in res.fetchall()]


async def propagate_to_new_entry(
    db: AsyncSession,
    *,
    entry_id: uuid.UUID,
    source_id: uuid.UUID,
    path: str,
) -> None:
    """A new entry just appeared via ingest; pick up any tags whose
    direct origin is a tagged ancestor directory.

    Thin wrapper over `propagate_to_new_entries` for callers (admin
    tag-apply paths, the move-rebalancer) that only have one entry.
    The hot path through ingest uses the bulk version directly.
    """
    await propagate_to_new_entries(
        db,
        new_entries=[(entry_id, source_id, path)],
    )


async def propagate_to_new_entries(
    db: AsyncSession,
    *,
    new_entries: list[tuple[uuid.UUID, uuid.UUID, str]],
) -> None:
    """Bulk variant: propagate tagged-ancestor inheritance to many new
    entries in one round-trip.

    Pre-v0.29.2 every Phase-3 ingest entry called the singular helper,
    which issued one SELECT per entry to find tagged ancestors. On a
    2000-row full-scan batch with 80% new entries, that was ~1600
    SELECTs per batch — measured at ~1 ms each, so 1.6 s of pure
    round-trip cost the walker was waiting on at the API side. This
    bulk variant computes the entire fan-out in a single query using
    PostgreSQL's `unnest(uuid[], text[])` to feed the new entries in
    as a derived table.

    Per-source loop: the JOIN to `entries` is bounded to one source
    at a time so the LIKE-pattern path lookups stay efficient
    (the existing path index is single-source). Mixed-source batches
    fan out into one round-trip per source — still a huge win over
    one round-trip per entry.

    `new_entries` is a list of (entry_id, source_id, path) tuples.
    No-op on empty input.
    """
    if not new_entries:
        return

    # Group by source_id so each query targets one source's path index.
    by_source: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
    for entry_id, source_id, path in new_entries:
        by_source.setdefault(source_id, []).append((entry_id, path))

    for source_id, items in by_source.items():
        eids = [eid for eid, _ in items]
        paths = [p for _, p in items]
        await db.execute(
            text(
                r"""
                INSERT INTO entry_tags
                    (id, entry_id, tag, inherited_from_entry_id, created_by_user_id)
                SELECT
                    gen_random_uuid(),
                    n.entry_id, et.tag, et.entry_id, et.created_by_user_id
                FROM unnest(CAST(:eids AS uuid[]), CAST(:paths AS text[]))
                     AS n(entry_id, path)
                JOIN entries anc
                  ON anc.source_id = :source_id
                 AND anc.kind = 'directory'
                 AND n.path LIKE
                     replace(replace(replace(anc.path, '\', '\\'), '%', '\%'), '_', '\_')
                     || '/%' ESCAPE '\'
                JOIN entry_tags et
                  ON et.entry_id = anc.id
                 AND et.inherited_from_entry_id IS NULL
                ON CONFLICT (entry_id, tag, inherited_from_entry_id) DO NOTHING
                """
            ),
            {"eids": eids, "paths": paths, "source_id": source_id},
        )


async def rebalance_on_move(
    db: AsyncSession,
    *,
    entry_id: uuid.UUID,
    new_source_id: uuid.UUID,
    new_path: str,
) -> list[uuid.UUID]:
    """An entry has moved (same content_hash, new (source_id, path)).
    Drop inherited rows whose ancestor no longer covers the new path,
    then re-materialise inheritance from any new tagged ancestors.

    Returns the (possibly empty) list of affected entry IDs for
    re-indexing. Only the moved entry can be affected — descendants
    keep their inherited rows since their own ancestors didn't move.
    """
    # 1) Drop inherited rows whose source-ancestor no longer covers
    # this entry's new path. Direct rows (`inherited_from_entry_id IS
    # NULL`) are untouched — moves don't strip a tag the user applied
    # directly.
    #
    # LIKE-pattern escape (review D-C3): the pattern side of LIKE is
    # `path || '/%'` where `path` is a directory's path from a row in
    # entries. If any directory's path contains '%' or '_', those
    # characters act as wildcards in the LIKE comparison and would
    # widen the IN-clause to include unrelated ancestors. Escape them
    # explicitly with the same chain we use in apply_tag and
    # propagate_to_new_entry.
    await db.execute(
        text(
            r"""
            DELETE FROM entry_tags
            WHERE entry_id = :entry_id
              AND inherited_from_entry_id IS NOT NULL
              AND inherited_from_entry_id NOT IN (
                  SELECT id FROM entries
                  WHERE source_id = :new_source_id
                    AND kind = 'directory'
                    AND :new_path LIKE
                        replace(replace(replace(path, '\', '\\'), '%', '\%'), '_', '\_')
                        || '/%' ESCAPE '\'
              )
            """
        ),
        {
            "entry_id": entry_id,
            "new_source_id": new_source_id,
            "new_path": new_path,
        },
    )

    # 2) Materialise any newly-applicable inherited rows.
    await propagate_to_new_entry(
        db,
        entry_id=entry_id,
        source_id=new_source_id,
        path=new_path,
    )
    return [entry_id]


async def get_tags_for_entry(
    db: AsyncSession, *, entry_id: uuid.UUID,
) -> list[dict]:
    """Return tags on `entry_id` grouped by origin. Each item is
    `{tag, inherited, inherited_from_path}` where `inherited` is True
    iff this row was materialised from an ancestor and
    `inherited_from_path` is that ancestor's path (None when direct).

    A given tag string may appear twice — once direct and once
    inherited — when a user has tagged both a directory and a child
    file with the same label. The UI dedups these for display.
    """
    res = await db.execute(
        text(
            """
            SELECT
                et.tag,
                et.inherited_from_entry_id IS NOT NULL AS inherited,
                anc.path AS inherited_from_path
            FROM entry_tags et
            LEFT JOIN entries anc ON anc.id = et.inherited_from_entry_id
            WHERE et.entry_id = :entry_id
            ORDER BY inherited ASC, et.tag ASC
            """
        ),
        {"entry_id": entry_id},
    )
    return [
        {
            "tag": row.tag,
            "inherited": bool(row.inherited),
            "inherited_from_path": row.inherited_from_path,
        }
        for row in res.fetchall()
    ]


async def get_tags_for_entries(
    db: AsyncSession, *, entry_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[str]]:
    """Bulk fetch — for each entry id, the deduped union of direct and
    inherited tag strings. Used by `build_entry_doc` so each Meili
    document carries the same effective tag set the SQL `tag`
    predicate would match against.
    """
    if not entry_ids:
        return {}
    res = await db.execute(
        text(
            """
            SELECT entry_id, tag
            FROM entry_tags
            WHERE entry_id = ANY(:entry_ids)
            """
        ),
        {"entry_ids": entry_ids},
    )
    out: dict[uuid.UUID, set[str]] = {eid: set() for eid in entry_ids}
    for row in res.fetchall():
        out[row.entry_id].add(row.tag)
    return {eid: sorted(tags) for eid, tags in out.items()}
