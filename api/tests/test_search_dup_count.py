"""Dup-count enrichment is bounded by the user's source filter (review I12).

Two files with the same content_hash, in two different sources both
permitted to the user. Searching unfiltered → dup_count counts both.
Searching with source_id=A → dup_count counts only A's copies."""

import uuid

import pytest


async def _register_login(client, username="alice", password="testpass123"):
    await client.post("/api/users/register", json={"username": username, "password": password})
    login = await client.post("/api/users/login", json={"username": username, "password": password})
    return login.json()["access_token"]


@pytest.mark.asyncio
async def test_dup_count_scoped_to_source_filter(client, db_session):
    """Two duplicate files across two sources. Source-filtered search
    must report dup_count from only the filtered source."""
    from akashic.models import Source, Entry

    token = await _register_login(client)

    src_a = Source(id=uuid.uuid4(), name="a", type="local", connection_config={"path": "/tmp/a"})
    src_b = Source(id=uuid.uuid4(), name="b", type="local", connection_config={"path": "/tmp/b"})
    db_session.add_all([src_a, src_b])
    await db_session.flush()

    same_hash = "deadbeef" * 8  # 64-char sha256-shape

    entries = [
        Entry(
            id=uuid.uuid4(), source_id=src_a.id, kind="file",
            path="/tmp/a/foo.pdf", parent_path="/tmp/a", name="foo.pdf",
            content_hash=same_hash,
        ),
        Entry(
            id=uuid.uuid4(), source_id=src_a.id, kind="file",
            path="/tmp/a/foo-copy.pdf", parent_path="/tmp/a", name="foo-copy.pdf",
            content_hash=same_hash,
        ),
        Entry(
            id=uuid.uuid4(), source_id=src_b.id, kind="file",
            path="/tmp/b/foo.pdf", parent_path="/tmp/b", name="foo.pdf",
            content_hash=same_hash,
        ),
    ]
    db_session.add_all(entries)
    await db_session.commit()

    # Force the SQL fallback path (regex mode) so the test works without
    # Meili. Pattern matches anything ending in .pdf in the entries' paths.
    unfiltered = await client.get(
        "/api/search?q=.pdf$&mode=regex",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert unfiltered.status_code == 200
    hits = unfiltered.json()["results"]
    # Each of the 3 entries reports the other 2 as dups.
    assert hits, "expected hits in unfiltered search"
    for h in hits:
        if h.get("content_hash") == same_hash:
            assert h["dup_count"] == 2, f"unfiltered: expected 2 dups, got {h['dup_count']}"

    # Source-filtered search: only Source A. Each Source-A hit should
    # see the other Source-A copy as a dup, NOT the Source-B copy.
    filtered = await client.get(
        f"/api/search?q=.pdf$&mode=regex&source_id={src_a.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert filtered.status_code == 200
    fhits = filtered.json()["results"]
    assert fhits, "expected hits in source-filtered search"
    for h in fhits:
        if h.get("content_hash") == same_hash:
            assert h["dup_count"] == 1, (
                f"source-scoped: expected 1 dup, got {h['dup_count']}"
            )
