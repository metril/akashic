"""Search modes (fuzzy / glob / regex) — added v0.5.11.

Modes other than fuzzy force the SQL fallback path because Meilisearch
can't express exact pattern matching against the path/name attributes.
These tests exercise the SQL path directly so they don't need Meili
running.
"""
import uuid

import pytest

from akashic.services.search import glob_to_sql_like, validate_regex


async def _register_login(client, username="alice", password="testpass123"):
    await client.post(
        "/api/users/register", json={"username": username, "password": password}
    )
    login = await client.post(
        "/api/users/login", json={"username": username, "password": password}
    )
    return login.json()["access_token"]


async def _seed_entries(db_session, paths: list[str]) -> uuid.UUID:
    """Insert a bunch of file entries on a fresh source. Returns the
    source id so the test can scope-filter if needed."""
    from akashic.models import Source, Entry

    source = Source(
        id=uuid.uuid4(),
        name="t",
        type="local",
        connection_config={"path": "/tmp"},
    )
    db_session.add(source)
    await db_session.flush()

    for p in paths:
        name = p.rsplit("/", 1)[-1]
        parent = p.rsplit("/", 1)[0] or "/"
        ext = name.rsplit(".", 1)[-1] if "." in name else None
        db_session.add(
            Entry(
                id=uuid.uuid4(),
                source_id=source.id,
                kind="file",
                path=p,
                parent_path=parent,
                name=name,
                extension=ext,
            )
        )
    await db_session.commit()
    return source.id


# --- glob_to_sql_like helper ---


def test_glob_to_sql_like_translates_star_to_percent():
    assert glob_to_sql_like("*.pdf") == "%.pdf"
    assert glob_to_sql_like("report*") == "report%"
    assert glob_to_sql_like("report*.pdf") == "report%.pdf"


def test_glob_to_sql_like_collapses_double_star():
    assert glob_to_sql_like("**/invoices/*") == "%/invoices/%"
    assert glob_to_sql_like("**/*.csv") == "%/%.csv"


def test_glob_to_sql_like_question_to_underscore():
    assert glob_to_sql_like("file?.txt") == "file_.txt"


def test_glob_to_sql_like_escapes_literal_sql_wildcards():
    assert glob_to_sql_like("100%-done.txt") == "100\\%-done.txt"
    assert glob_to_sql_like("snake_case.py") == "snake\\_case.py"


# --- validate_regex helper ---


def test_validate_regex_accepts_valid():
    validate_regex(r"^[0-9]{4}\.pdf$")
    validate_regex(r"report-\d+")


def test_validate_regex_rejects_invalid():
    import re as _re
    with pytest.raises(_re.error):
        validate_regex("[invalid")


# --- end-to-end: glob mode against the SQL fallback ---


@pytest.mark.asyncio
async def test_search_glob_matches_filename_pattern(client, db_session):
    token = await _register_login(client)
    await _seed_entries(
        db_session,
        ["/data/report-2024.pdf", "/data/summary.pdf", "/data/report-2025.pdf"],
    )
    r = await client.get(
        "/api/search?q=report*.pdf&mode=glob",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    names = {h["filename"] for h in r.json()["results"]}
    assert names == {"report-2024.pdf", "report-2025.pdf"}


@pytest.mark.asyncio
async def test_search_glob_double_star_matches_across_paths(client, db_session):
    token = await _register_login(client)
    await _seed_entries(
        db_session,
        [
            "/data/2024/q1/invoices/jan.csv",
            "/data/2025/invoices/feb.csv",
            "/data/2024/q1/reports/jan.csv",
        ],
    )
    r = await client.get(
        "/api/search?q=**/invoices/*&mode=glob",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    names = {h["filename"] for h in r.json()["results"]}
    assert names == {"jan.csv", "feb.csv"}


# --- end-to-end: regex mode ---


@pytest.mark.asyncio
async def test_search_regex_matches_path_pattern(client, db_session):
    token = await _register_login(client)
    await _seed_entries(
        db_session,
        ["/data/2024-Q1.pdf", "/data/2025-Q3.pdf", "/data/notes.pdf"],
    )
    r = await client.get(
        "/api/search?q=^/data/[0-9]{4}-Q[1-4]\\.pdf$&mode=regex",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    names = {h["filename"] for h in r.json()["results"]}
    assert names == {"2024-Q1.pdf", "2025-Q3.pdf"}


@pytest.mark.asyncio
async def test_search_regex_invalid_returns_400(client):
    token = await _register_login(client)
    r = await client.get(
        "/api/search?q=[invalid&mode=regex",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400
    assert "regex" in r.json()["detail"].lower()


# --- fuzzy regression ---


@pytest.mark.asyncio
async def test_search_fuzzy_default_unchanged(client, db_session):
    """Default mode is fuzzy. We can't reliably assert content because
    fuzzy hits Meili first and the test Meili index is empty (the SQL
    fallback only runs on Meili exceptions, not zero-results); the goal
    here is that adding the `mode` param doesn't break the existing
    contract — same response shape, same 200 status, no regression."""
    token = await _register_login(client)
    await _seed_entries(db_session, ["/data/invoice.pdf", "/data/report.pdf"])
    r = await client.get(
        "/api/search?q=invoice",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "results" in body
    assert "total" in body
    assert "query" in body
    assert body["query"] == "invoice"


@pytest.mark.asyncio
async def test_search_unknown_mode_rejected(client):
    token = await _register_login(client)
    r = await client.get(
        "/api/search?q=anything&mode=destroy_everything",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
