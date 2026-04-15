from pydantic import BaseModel

from akashic.schemas.file import FileResponse


class SearchResults(BaseModel):
    results: list[FileResponse]
    total: int
    query: str
