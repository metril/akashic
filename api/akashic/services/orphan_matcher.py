"""Match orphaned entries (source_id IS NULL) against the freshly-
scanned entries of a target source so they can be re-attached.

Two strategies:

  - "path"          — match on (path, name, kind) only. Cheap,
                      typical case for "I deleted the source and
                      re-created it pointing at the same backend".
  - "path_and_hash" — additionally require content_hash equality.
                      Stricter; useful when the operator wants to
                      be sure the file at this path is actually the
                      same file (not e.g. a re-derivation that
                      happens to share the path). Orphans without a
                      hash never match — `--full` scan first if you
                      want hash-strict matching.

Conflicts (path matches, hash differs) and ambiguous cases (multiple
orphans share the same path) are surfaced separately so the
operator's preview shows the breakdown before they commit.

The match logic is a pure function of the DB state — easy to
test without an api round-trip. The router thin-wraps it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.models.entry import Entry


Strategy = Literal["path", "path_and_hash"]


@dataclass(frozen=True)
class MatchPair:
    fresh_id: uuid.UUID
    orphan_id: uuid.UUID


@dataclass(frozen=True)
class MatchSummary:
    """Result of a dry-run match. `pairs` is the actionable set
    (fresh+orphan that would be merged on commit); `conflicts` and
    `ambiguous` are counts the operator should know about before
    committing."""
    pairs: list[MatchPair]
    conflicts: int        # path matches but hash differs
    ambiguous: int        # multiple orphans at same path → unmatched

    @property
    def matched(self) -> int:
        return len(self.pairs)


# Pulled out for testability + reuse between dry-run and commit.
# Both flavours share the same path/name/kind base join; the
# hash flavour adds an `AND fresh.content_hash = orphan.content_hash`
# clause and excludes orphans without a hash.
_MATCH_BASE = """
WITH candidates AS (
  SELECT
    fresh.id          AS fresh_id,
    orphan.id         AS orphan_id,
    fresh.content_hash  AS fresh_hash,
    orphan.content_hash AS orphan_hash,
    COUNT(*) OVER (PARTITION BY fresh.id) AS dups_for_fresh
  FROM entries fresh
  JOIN entries orphan
    ON orphan.source_id IS NULL
   AND orphan.path = fresh.path
   AND orphan.name = fresh.name
   AND orphan.kind = fresh.kind
  WHERE fresh.source_id = :source_id
)
SELECT fresh_id, orphan_id, fresh_hash, orphan_hash, dups_for_fresh
FROM candidates
"""


async def find_matches(
    db: AsyncSession,
    source_id: uuid.UUID,
    strategy: Strategy = "path",
) -> MatchSummary:
    """Compute which orphans could be re-attached to `source_id`.

    For every fresh entry in the target source, look for an orphan
    sharing (path, name, kind). Disambiguate as follows:

      - 0 orphans match → not interesting; not in any bucket.
      - 1 orphan, hash agreement (or strategy='path' and we
        ignore hashes) → goes into `pairs`.
      - 1 orphan, hashes both present and differ → `conflicts++`.
      - >1 orphans → `ambiguous++`. We could pick one (e.g. by
        first_seen_at) but that's a value judgement; safer to
        surface it and let the operator decide manually.
    """
    rows = (await db.execute(
        text(_MATCH_BASE).bindparams(
            bindparam("source_id", source_id),
        )
    )).all()

    pairs: list[MatchPair] = []
    conflicts = 0
    # Track ambiguous fresh-ids in a set so we count fresh entries
    # (not candidate rows). Two orphans at the same path produce
    # two rows that are both ambiguous — but it's one ambiguous
    # *case* from the operator's POV.
    ambiguous_fresh: set = set()
    for r in rows:
        fresh_id, orphan_id, fresh_hash, orphan_hash, dups = r
        if dups > 1:
            ambiguous_fresh.add(fresh_id)
            continue
        # Hash check. The strategy decides whether we treat a
        # mismatch as "conflict" (don't match) or just ignore the
        # hash entirely.
        if strategy == "path_and_hash":
            if orphan_hash is None:
                # Orphan never had a hash → we can't apply the
                # strict strategy, treat as a non-match.
                continue
            if fresh_hash is not None and fresh_hash != orphan_hash:
                conflicts += 1
                continue
        else:
            # path-only: still surface the conflict count for the
            # operator's situational awareness, but DON'T exclude
            # the pair (the user picked path-only knowing the file
            # might have changed).
            if (fresh_hash is not None
                and orphan_hash is not None
                and fresh_hash != orphan_hash):
                conflicts += 1
                # Still match on path — caller chose path-only.
        pairs.append(MatchPair(fresh_id=fresh_id, orphan_id=orphan_id))
    return MatchSummary(
        pairs=pairs,
        conflicts=conflicts,
        ambiguous=len(ambiguous_fresh),
    )


async def commit_matches(
    db: AsyncSession,
    source_id: uuid.UUID,
    pairs: list[MatchPair],
) -> list[uuid.UUID]:
    """Re-attach each (fresh, orphan) pair into `source_id`. Returns
    the list of orphan ids that were successfully re-attached
    (these are also the entry ids the search index should be
    partial-updated with).

    Per pair:
      1. Capture the fresh row's fields we want to carry forward.
      2. DELETE the fresh row so its `(source_id, path)` slot
         frees up. (The unique constraint blocks the orphan from
         taking source_id otherwise — orphan + fresh would both
         claim the same key.)
      3. UPDATE orphan: set source_id and merge in the captured
         fresh fields (COALESCE so we don't lose orphan-side data
         the fresh scan didn't compute, e.g. on incremental scans).

    The orphan keeps its history (tags, version history, audit
    trail) and gains whatever the fresh scan added.
    """
    if not pairs:
        return []
    reattached: list[uuid.UUID] = []
    for p in pairs:
        # 1. Snapshot fresh fields.
        snap = (await db.execute(
            text(
                "SELECT mime_type, size_bytes, fs_modified_at, content_hash "
                "  FROM entries WHERE id = :fresh_id"
            ).bindparams(bindparam("fresh_id", p.fresh_id))
        )).first()
        if snap is None:
            # Fresh row vanished between dry-run and commit (concurrent
            # delete?). Skip — the orphan can be recovered later.
            continue
        fresh_mime, fresh_size, fresh_mtime, fresh_hash = snap

        # 2. Free the unique-constraint slot.
        await db.execute(
            text("DELETE FROM entries WHERE id = :fresh_id").bindparams(
                bindparam("fresh_id", p.fresh_id),
            )
        )

        # 3. Re-attach the orphan into source_id, merging fresh fields.
        await db.execute(
            text("""
                UPDATE entries
                   SET source_id      = :source_id,
                       mime_type      = COALESCE(:fresh_mime, mime_type),
                       size_bytes     = COALESCE(:fresh_size, size_bytes),
                       fs_modified_at = COALESCE(:fresh_mtime, fs_modified_at),
                       content_hash   = COALESCE(:fresh_hash, content_hash)
                 WHERE id = :orphan_id
            """).bindparams(
                bindparam("source_id", source_id),
                bindparam("fresh_mime", fresh_mime),
                bindparam("fresh_size", fresh_size),
                bindparam("fresh_mtime", fresh_mtime),
                bindparam("fresh_hash", fresh_hash),
                bindparam("orphan_id", p.orphan_id),
            )
        )
        reattached.append(p.orphan_id)
    return reattached


async def count_potential_matches(
    db: AsyncSession, source_id: uuid.UUID,
) -> int:
    """Cheap COUNT used by the source-detail banner to decide
    whether to surface the 'Recover orphans' affordance. Counts
    distinct orphans that share a path with at least one fresh
    entry — doesn't try to disambiguate or check hashes."""
    res = await db.execute(
        text("""
            SELECT COUNT(DISTINCT orphan.id)
              FROM entries fresh
              JOIN entries orphan
                ON orphan.source_id IS NULL
               AND orphan.path = fresh.path
               AND orphan.name = fresh.name
               AND orphan.kind = fresh.kind
             WHERE fresh.source_id = :source_id
        """).bindparams(bindparam("source_id", source_id))
    )
    return int(res.scalar_one() or 0)
