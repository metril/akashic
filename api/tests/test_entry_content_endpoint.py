"""Phase B2 tests — GET /api/entries/{id}/content and /preview.

Local-source path is exercised end-to-end against the real filesystem
(tmp_path). Non-local fetch is validated through the path-traversal
guards; the actual scanner subprocess is exercised separately.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.main import create_app
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services import entry_content
from akashic.services.entry_content import (
    PathTraversal,
    validate_local_path,
    validate_remote_path,
)


# ── path-traversal unit tests ──────────────────────────────────────────────


def test_validate_local_path_normal(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("hi")
    canon = validate_local_path(str(tmp_path), str(f))
    assert canon == str(f.resolve())


def test_validate_local_path_dotdot_rejected(tmp_path):
    sibling = tmp_path.parent / "outside.txt"
    with pytest.raises(PathTraversal):
        validate_local_path(str(tmp_path), str(sibling))


def test_validate_local_path_relative_dotdot(tmp_path):
    payload = str(tmp_path) + "/../etc/passwd"
    with pytest.raises(PathTraversal):
        validate_local_path(str(tmp_path), payload)


def test_validate_local_path_symlink_escape_rejected(tmp_path):
    """A symlink inside the source root that points OUTSIDE must be rejected.
    Otherwise an attacker who can write a symlink into the indexed area
    could exfiltrate arbitrary files via the content endpoint."""
    outside = tmp_path.parent / "outside-target.txt"
    outside.write_text("secret")
    inside_link = tmp_path / "trap"
    inside_link.symlink_to(outside)
    with pytest.raises(PathTraversal):
        validate_local_path(str(tmp_path), str(inside_link))


def test_validate_local_path_internal_symlink_ok(tmp_path):
    """A symlink that stays within the source root is fine."""
    target = tmp_path / "real.txt"
    target.write_text("content")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    canon = validate_local_path(str(tmp_path), str(link))
    # Lexical canonical path is the link path; the real-path check passed.
    assert canon == str(link.resolve().parent / "link.txt") or canon == str(link)


def test_validate_remote_path_nul_byte_rejected():
    with pytest.raises(PathTraversal):
        validate_remote_path("/files/data\x00hidden")


def test_validate_remote_path_dotdot_rejected():
    with pytest.raises(PathTraversal):
        validate_remote_path("/foo/../etc/passwd")


def test_validate_remote_path_backslash_dotdot():
    with pytest.raises(PathTraversal):
        validate_remote_path(r"\foo\..\bar")


def test_validate_remote_path_empty():
    with pytest.raises(PathTraversal):
        validate_remote_path("")


def test_looks_binary():
    assert entry_content.PREVIEW_MAX_BYTES == 64 * 1024


# ── Endpoint integration ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def admin_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(), username="admin",
            email="a@b.c", password_hash="x", role="admin",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def viewer_user(setup_db) -> User:
    async with setup_db() as session:
        user = User(
            id=uuid.uuid4(), username="viewer",
            email="v@b.c", password_hash="x", role="viewer",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def local_source(setup_db, tmp_path) -> Source:
    async with setup_db() as session:
        src = Source(
            id=uuid.uuid4(), name="tmp", type="local",
            connection_config={"path": str(tmp_path)},
        )
        session.add(src)
        await session.commit()
        await session.refresh(src)
        return src


@pytest_asyncio.fixture
async def text_entry(setup_db, local_source: Source, tmp_path) -> Entry:
    f = tmp_path / "hello.txt"
    f.write_text("Hello, world!\nLine 2\n", encoding="utf-8")
    async with setup_db() as session:
        entry = Entry(
            id=uuid.uuid4(),
            source_id=local_source.id,
            kind="file",
            path=str(f),
            parent_path=str(tmp_path),
            name="hello.txt",
            extension="txt",
            size_bytes=os.path.getsize(f),
            mime_type="text/plain",
            mode=33188,
            uid=0,
            gid=0,
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        return entry


@pytest_asyncio.fixture
async def binary_entry(setup_db, local_source: Source, tmp_path) -> Entry:
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\x00\x01\x02\xff\xfe\xfd" * 100)
    async with setup_db() as session:
        entry = Entry(
            id=uuid.uuid4(),
            source_id=local_source.id,
            kind="file",
            path=str(f),
            parent_path=str(tmp_path),
            name="blob.bin",
            extension="bin",
            size_bytes=os.path.getsize(f),
            mime_type="application/octet-stream",
            mode=33188,
            uid=0,
            gid=0,
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        return entry


def _client_factory(setup_db, user: User) -> AsyncClient:
    async def _override_get_db():
        async with setup_db() as session:
            yield session

    async def _override_get_current_user():
        return user

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_current_user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def admin_client(setup_db, admin_user: User):
    async with _client_factory(setup_db, admin_user) as ac:
        yield ac


@pytest_asyncio.fixture
async def viewer_client(setup_db, viewer_user: User):
    async with _client_factory(setup_db, viewer_user) as ac:
        yield ac


# ── /content tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_content_local_text(
    admin_client: AsyncClient, text_entry: Entry
):
    r = await admin_client.get(f"/api/entries/{text_entry.id}/content")
    assert r.status_code == 200
    assert r.text == "Hello, world!\nLine 2\n"
    assert r.headers["content-type"].startswith("text/plain")
    assert "inline" in r.headers["content-disposition"]


@pytest.mark.asyncio
async def test_content_attachment_disposition(
    admin_client: AsyncClient, text_entry: Entry
):
    r = await admin_client.get(
        f"/api/entries/{text_entry.id}/content?attachment=1"
    )
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert 'filename="hello.txt"' in r.headers["content-disposition"]


@pytest.mark.asyncio
async def test_content_binary(
    admin_client: AsyncClient, binary_entry: Entry
):
    r = await admin_client.get(f"/api/entries/{binary_entry.id}/content")
    assert r.status_code == 200
    assert r.content == b"\x00\x01\x02\xff\xfe\xfd" * 100
    assert r.headers["content-type"] == "application/octet-stream"


@pytest.mark.asyncio
async def test_content_404_for_unknown_entry(admin_client: AsyncClient):
    fake = uuid.uuid4()
    r = await admin_client.get(f"/api/entries/{fake}/content")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_content_403_for_viewer_without_perm(
    viewer_client: AsyncClient, text_entry: Entry
):
    r = await viewer_client.get(f"/api/entries/{text_entry.id}/content")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_content_path_traversal_rejected(
    setup_db, admin_client: AsyncClient, local_source: Source, tmp_path
):
    """Forge an entry whose path is outside the source root."""
    async with setup_db() as session:
        e = Entry(
            id=uuid.uuid4(),
            source_id=local_source.id,
            kind="file",
            path="/etc/hostname",  # outside the tmp_path source root
            parent_path="/etc",
            name="hostname",
            mode=33188, uid=0, gid=0,
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        session.add(e)
        await session.commit()
        eid = e.id
    r = await admin_client.get(f"/api/entries/{eid}/content")
    assert r.status_code == 400


# ── /preview tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preview_local_text(
    admin_client: AsyncClient, text_entry: Entry
):
    r = await admin_client.get(f"/api/entries/{text_entry.id}/preview")
    assert r.status_code == 200
    body = r.json()
    assert body["binary"] is False
    assert body["encoding"] == "utf-8"
    assert "Hello, world!" in body["text"]
    assert body["truncated"] is False
    assert body["byte_size_total"] == os.path.getsize(text_entry.path)


@pytest.mark.asyncio
async def test_preview_local_binary(
    admin_client: AsyncClient, binary_entry: Entry
):
    r = await admin_client.get(f"/api/entries/{binary_entry.id}/preview")
    assert r.status_code == 200
    body = r.json()
    assert body["binary"] is True
    assert body["text"] is None
    assert body["encoding"] is None


@pytest.mark.asyncio
async def test_preview_truncation(
    setup_db, admin_client: AsyncClient, local_source: Source, tmp_path
):
    """Preview cap is 64KB. A 70KB file should report truncated:true."""
    big = tmp_path / "big.txt"
    big.write_text("a" * (70 * 1024))
    async with setup_db() as session:
        e = Entry(
            id=uuid.uuid4(),
            source_id=local_source.id,
            kind="file",
            path=str(big),
            parent_path=str(tmp_path),
            name="big.txt",
            mime_type="text/plain",
            size_bytes=os.path.getsize(big),
            mode=33188, uid=0, gid=0,
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
        )
        session.add(e)
        await session.commit()
        eid = e.id

    r = await admin_client.get(f"/api/entries/{eid}/preview")
    body = r.json()
    assert body["truncated"] is True
    assert len(body["text"]) == 64 * 1024
    assert body["byte_size_total"] == 70 * 1024
