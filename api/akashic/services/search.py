import re
from typing import TYPE_CHECKING

from meilisearch_python_sdk import AsyncClient
from meilisearch_python_sdk.models.settings import Pagination

from akashic.config import settings

if TYPE_CHECKING:
    from akashic.models.entry import Entry

INDEX_NAME = "files"

# v0.6.0 — Tier 3 self-hosted libraries (Paperless-ngx, Immich) emit
# provider-specific metadata in `entries.domain_metadata`. The keys
# below are mirrored into the index doc as flat fields prefixed
# `domain_metadata__`, then registered as filterable attributes so
# users can chip on `domain_metadata__correspondent = "Bank"`. Connectors
# that emit other keys still index — those values just aren't filterable
# until the key joins this list.
#
# Underscore separator (not dot) because Meilisearch treats `.` in
# attribute names as nested-object access; a flat field with a dotted
# name would index the wrong shape.
DOMAIN_METADATA_FACET_KEYS: tuple[str, ...] = (
    "correspondent",      # Paperless
    "document_type",      # Paperless
    "tags",               # Paperless library tags (multi-valued)
    "person",             # Immich (face recognition label, multi-valued)
    "album",              # Immich
    "camera_make",        # Immich EXIF
    "camera_model",       # Immich EXIF
)


def _domain_metadata_doc_field(key: str) -> str:
    return f"domain_metadata__{key}"


def glob_to_sql_like(pattern: str) -> str:
    """Translate a glob pattern to a SQL LIKE pattern.

    `**` and `*` both collapse to `%` because LIKE has no path-segment
    awareness; users get "matches across path segments" either way,
    which matches their expectation when typing `**/invoices/*`.
    `?` becomes `_`. Literal `%` and `_` in the input are escaped so
    they don't accidentally widen the match.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                out.append("%")
                i += 2
                continue
            out.append("%")
        elif ch == "?":
            out.append("_")
        elif ch == "%":
            out.append("\\%")
        elif ch == "_":
            out.append("\\_")
        elif ch == "\\":
            out.append("\\\\")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def validate_regex(pattern: str) -> None:
    """Raise re.error with a useful message if the regex is malformed.
    Caller wraps the raise into HTTP 400."""
    re.compile(pattern)

# v0.4.14 — raise Meilisearch's deep-pagination cap from its 1000
# default. The cap exists to bound per-query memory; 100k is well
# within what a single index handles for our workload, and 1000 is
# too tight for the new infinite-scroll Search page. The frontend
# mirrors this constant to surface "showing top N — refine your
# query" when the cap is reached.
MAX_TOTAL_HITS = 100_000


async def get_meili_client() -> AsyncClient:
    return AsyncClient(settings.meili_url, settings.meili_key)


async def ensure_index():
    client = await get_meili_client()
    try:
        await client.get_index(INDEX_NAME)
    except Exception:
        await client.create_index(INDEX_NAME, primary_key="id")
    index = await client.get_index(INDEX_NAME)
    await index.update_searchable_attributes(["filename", "path", "content_text", "tags"])
    filterable = [
        "source_id", "extension", "mime_type", "size_bytes",
        "fs_modified_at", "tags", "owner_name", "group_name",
        "viewable_by_read", "viewable_by_write", "viewable_by_delete",
    ]
    filterable.extend(_domain_metadata_doc_field(k) for k in DOMAIN_METADATA_FACET_KEYS)
    await index.update_filterable_attributes(filterable)
    await index.update_sortable_attributes(["size_bytes", "fs_modified_at", "filename"])
    await index.update_pagination(Pagination(max_total_hits=MAX_TOTAL_HITS))


def build_entry_doc(
    entry: "Entry",
    content_text: str | None = None,
    *,
    tags: list[str] | None = None,
) -> dict:
    """Builds the Meili document for an Entry, including denormalized ACL arrays.

    Reads the pre-computed `viewable_by_*` columns when populated (the
    common case after Phase 4 ingest), and falls back to recomputing from
    `acl/mode/uid/gid` only for legacy rows that haven't been backfilled
    yet — same source of truth either way.

    `tags` carries the deduped union of direct + inherited tag strings
    on this entry. Phase C callers fetch them from EntryTag rows via
    `services/tag_inheritance.get_tags_for_entries`. Defaults to `[]`
    for callers that don't pre-fetch (e.g. legacy code paths) — the
    field stays present on the doc so the Meilisearch filterable
    attribute schema doesn't fluctuate.
    """
    from akashic.services.ingest import compute_viewable_buckets

    if entry.viewable_by_read is not None:
        read = entry.viewable_by_read
        write = entry.viewable_by_write or []
        delete = entry.viewable_by_delete or []
    else:
        buckets = compute_viewable_buckets(entry.acl, entry.mode, entry.uid, entry.gid)
        read, write, delete = buckets["read"], buckets["write"], buckets["delete"]

    doc: dict = {
        "id": str(entry.id),
        # Nullable since v0.4.0 — `str(None)` would index the literal
        # string "None" and silently break every source_id="<uuid>"
        # filter. Real JSON null instead.
        "source_id": str(entry.source_id) if entry.source_id else None,
        "path": entry.path,
        "filename": entry.name,
        "extension": entry.extension,
        "mime_type": entry.mime_type,
        "size_bytes": entry.size_bytes,
        "owner_name": entry.owner_name,
        "group_name": entry.group_name,
        "fs_modified_at": int(entry.fs_modified_at.timestamp())
            if entry.fs_modified_at else None,
        "tags": list(tags or []),
        "viewable_by_read":   read,
        "viewable_by_write":  write,
        "viewable_by_delete": delete,
    }
    if content_text is not None:
        doc["content_text"] = content_text
    # v0.6.0 — flatten the well-known domain_metadata keys into top-level
    # doc fields so they're individually filterable. v0.7.0 extends this
    # to handle multi-valued keys (`tags`, `person`) by emitting the
    # full string list — Meilisearch indexes each element as a
    # filterable value and `field = "x"` matches if any element equals
    # x. Dict values and arbitrary nested shapes are still dropped
    # from the index; they remain in postgres and render in the entry
    # detail drawer's Library Metadata section.
    dm = entry.domain_metadata or {}
    if isinstance(dm, dict):
        for key in DOMAIN_METADATA_FACET_KEYS:
            value = dm.get(key)
            if value is None:
                continue
            if isinstance(value, list):
                # Only string-arrays index cleanly. Numeric / mixed
                # lists are unusual for the current keys; coerce
                # everything to str for safety.
                values = [str(v) for v in value if v is not None]
                if values:
                    doc[_domain_metadata_doc_field(key)] = values
            elif not isinstance(value, dict):
                doc[_domain_metadata_doc_field(key)] = value
    return doc


async def index_file(file_data: dict):
    client = await get_meili_client()
    index = await client.get_index(INDEX_NAME)
    await index.add_documents([file_data])


async def index_files_batch(files: list[dict]):
    if not files:
        return
    client = await get_meili_client()
    index = await client.get_index(INDEX_NAME)
    await index.add_documents(files)


async def search_files(query: str, filters: str | None = None, sort: list[str] | None = None,
                       offset: int = 0, limit: int = 20,
                       facets: list[str] | None = None) -> dict:
    client = await get_meili_client()
    index = await client.get_index(INDEX_NAME)
    return await index.search(
        query, filter=filters, sort=sort, offset=offset, limit=limit,
        facets=facets,
    )


async def delete_file_from_index(file_id: str):
    client = await get_meili_client()
    index = await client.get_index(INDEX_NAME)
    await index.delete_document(file_id)


async def delete_files_batch(file_ids: list[str]) -> None:
    """Bulk-delete docs from the search index. Used by the
    `purge_entries` flavour of source delete (removes both the
    Postgres rows and the search-index docs in one round trip)."""
    if not file_ids:
        return
    client = await get_meili_client()
    index = await client.get_index(INDEX_NAME)
    await index.delete_documents(file_ids)


async def update_files_partial(docs: list[dict]) -> None:
    """Partial-update docs by id. Each doc must include `id` plus
    only the fields that should change. Used by the source-delete
    "preserve entries" path to null out source_id on the affected
    docs without rebuilding the full doc.

    Meili's `add_documents` is upsert-by-id and merges on the
    primary key, so partial updates work the same way as full
    additions — we just pass a sparse doc.
    """
    if not docs:
        return
    client = await get_meili_client()
    index = await client.get_index(INDEX_NAME)
    await index.update_documents(docs)
