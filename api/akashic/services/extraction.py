import httpx

from akashic.config import settings


async def extract_text_tika(content: bytes, mime_type: str = "application/octet-stream") -> str | None:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{settings.tika_url}/tika",
                content=content,
                headers={"Content-Type": mime_type, "Accept": "text/plain"},
                timeout=60.0,
            )
            if response.status_code == 200:
                text = response.text.strip()
                return text if text else None
    except Exception:
        return None
    return None


def extract_text_plain(content: bytes) -> str | None:
    try:
        return content.decode("utf-8").strip() or None
    except UnicodeDecodeError:
        try:
            return content.decode("latin-1").strip() or None
        except Exception:
            return None


PLAIN_TEXT_TYPES = {
    "text/plain", "text/html", "text/css", "text/javascript", "text/xml",
    "application/json", "application/xml", "application/javascript",
    "application/x-yaml", "application/toml",
}

TIKA_TYPES = {
    "application/pdf", "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-excel", "application/vnd.ms-powerpoint",
    "application/rtf", "application/epub+zip",
}


async def extract_text(content: bytes, mime_type: str) -> str | None:
    if mime_type in PLAIN_TEXT_TYPES or mime_type.startswith("text/"):
        return extract_text_plain(content)
    if mime_type in TIKA_TYPES:
        return await extract_text_tika(content, mime_type)
    return None
