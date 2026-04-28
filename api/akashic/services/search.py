from typing import TYPE_CHECKING

from meilisearch_python_sdk import AsyncClient

from akashic.config import settings

if TYPE_CHECKING:
    from akashic.models.entry import Entry

INDEX_NAME = "files"


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
        await index.update_filterable_attributes([
            "source_id", "extension", "mime_type", "size_bytes",
            "fs_modified_at", "tags", "owner_name", "group_name",
            "viewable_by_read", "viewable_by_write", "viewable_by_delete",
        ])
        await index.update_sortable_attributes(["size_bytes", "fs_modified_at", "filename"])


def build_entry_doc(entry: "Entry", content_text: str | None = None) -> dict:
    """Builds the Meili document for an Entry, including denormalized ACL arrays."""
    from akashic.services.acl_denorm import denormalize_acl
    from akashic.schemas.acl import ACL
    from pydantic import TypeAdapter

    acl_obj = None
    if entry.acl:
        try:
            acl_obj = TypeAdapter(ACL).validate_python(entry.acl)
        except Exception:
            acl_obj = None
    buckets = denormalize_acl(
        acl=acl_obj,
        base_mode=entry.mode,
        base_uid=entry.uid,
        base_gid=entry.gid,
    )

    doc: dict = {
        "id": str(entry.id),
        "source_id": str(entry.source_id),
        "path": entry.path,
        "filename": entry.name,
        "extension": entry.extension,
        "mime_type": entry.mime_type,
        "size_bytes": entry.size_bytes,
        "owner_name": entry.owner_name,
        "group_name": entry.group_name,
        "fs_modified_at": int(entry.fs_modified_at.timestamp())
            if entry.fs_modified_at else None,
        "tags": [],
        "viewable_by_read":   buckets["read"],
        "viewable_by_write":  buckets["write"],
        "viewable_by_delete": buckets["delete"],
    }
    if content_text is not None:
        doc["content_text"] = content_text
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
                       offset: int = 0, limit: int = 20) -> dict:
    client = await get_meili_client()
    index = await client.get_index(INDEX_NAME)
    return await index.search(query, filter=filters, sort=sort, offset=offset, limit=limit)


async def delete_file_from_index(file_id: str):
    client = await get_meili_client()
    index = await client.get_index(INDEX_NAME)
    await index.delete_document(file_id)
