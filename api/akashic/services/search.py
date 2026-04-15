from meilisearch_python_sdk import AsyncClient

from akashic.config import settings

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
            "source_id", "extension", "mime_type", "size_bytes", "fs_modified_at", "tags",
        ])
        await index.update_sortable_attributes(["size_bytes", "fs_modified_at", "filename"])


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
