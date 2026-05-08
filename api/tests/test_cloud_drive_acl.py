"""v0.13.0 — Tier 1 PR-B: native_id + cloud_drive ACL plumbing.

Smoke-tests for the schema additions that the upcoming Drive / OneDrive /
SharePoint connectors will populate. No live provider is involved — this
verifies the Python side of the contract: discriminator parsing,
denormalization to viewable_by_* tokens, and effective-perms evaluation.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import create_ingest_token
from tests.conftest import seed_scan
from akashic.models.entry import Entry
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.acl import ACL, CloudDriveACL
from akashic.schemas.effective import GroupRef, PrincipalRef
from akashic.services.acl_denorm import denormalize_acl, ANYONE
from akashic.services.effective_perms import compute_effective


# ---------------------------------------------------------------------------
# Discriminator + parsing.
# ---------------------------------------------------------------------------


def test_cloud_drive_acl_discriminator_parses():
    raw = {
        "type": "cloud_drive",
        "domain_restricted_to": "example.com",
        "grants": [
            {
                "principal": {
                    "type": "user",
                    "id": "perm-123",
                    "email": "alice@example.com",
                    "name": "Alice",
                },
                "role": "writer",
            },
            {
                "principal": {"type": "anyone", "id": "anyone"},
                "role": "reader",
                "link": {"id": "lnk-1", "scope": "anyone"},
            },
        ],
    }
    adapter: TypeAdapter[ACL] = TypeAdapter(ACL)
    parsed = adapter.validate_python(raw)
    assert isinstance(parsed, CloudDriveACL)
    assert parsed.domain_restricted_to == "example.com"
    assert len(parsed.grants) == 2
    assert parsed.grants[0].principal.type == "user"
    assert parsed.grants[1].link is not None
    assert parsed.grants[1].link.scope == "anyone"


def test_cloud_drive_acl_rejects_unknown_role():
    raw = {
        "type": "cloud_drive",
        "grants": [
            {
                "principal": {"type": "user", "id": "x"},
                "role": "executor-not-a-thing",
            }
        ],
    }
    adapter: TypeAdapter[ACL] = TypeAdapter(ACL)
    with pytest.raises(Exception):
        adapter.validate_python(raw)


# ---------------------------------------------------------------------------
# Effective-perms evaluation.
# ---------------------------------------------------------------------------


def _acl(grants: list[dict], **extra) -> CloudDriveACL:
    raw = {"type": "cloud_drive", "grants": grants, **extra}
    return TypeAdapter(ACL).validate_python(raw)  # type: ignore[return-value]


def test_writer_grant_yields_read_write_delete():
    acl = _acl([
        {
            "principal": {
                "type": "user",
                "id": "perm-alice",
                "email": "alice@example.com",
            },
            "role": "writer",
        }
    ])
    perms = compute_effective(
        acl=acl,
        base_mode=None,
        base_uid=None,
        base_gid=None,
        principal=PrincipalRef(
            type="cloud_drive_user", identifier="alice@example.com"
        ),
    )
    assert perms.rights["read"].granted
    assert perms.rights["write"].granted
    assert perms.rights["delete"].granted
    assert not perms.rights["change_perms"].granted


def test_reader_grant_yields_read_only():
    acl = _acl([
        {
            "principal": {"type": "user", "id": "p", "email": "bob@example.com"},
            "role": "reader",
        }
    ])
    perms = compute_effective(
        acl=acl,
        base_mode=None,
        base_uid=None,
        base_gid=None,
        principal=PrincipalRef(
            type="cloud_drive_user", identifier="bob@example.com"
        ),
    )
    assert perms.rights["read"].granted
    assert not perms.rights["write"].granted
    assert not perms.rights["delete"].granted


def test_owner_grant_includes_change_perms():
    acl = _acl([
        {
            "principal": {"type": "user", "id": "p", "email": "carol@example.com"},
            "role": "owner",
        }
    ])
    perms = compute_effective(
        acl=acl,
        base_mode=None,
        base_uid=None,
        base_gid=None,
        principal=PrincipalRef(
            type="cloud_drive_user", identifier="carol@example.com"
        ),
    )
    assert perms.rights["change_perms"].granted


def test_anyone_grant_matches_anyone_principal():
    acl = _acl([
        {
            "principal": {"type": "anyone", "id": "anyone"},
            "role": "reader",
            "link": {"id": "l", "scope": "anyone"},
        }
    ])
    perms = compute_effective(
        acl=acl,
        base_mode=None,
        base_uid=None,
        base_gid=None,
        principal=PrincipalRef(
            type="cloud_drive_user", identifier="random@somewhere.io"
        ),
    )
    assert perms.rights["read"].granted


def test_domain_grant_matches_email_domain():
    acl = _acl([
        {
            "principal": {"type": "domain", "id": "example.com"},
            "role": "reader",
        }
    ])
    perms = compute_effective(
        acl=acl,
        base_mode=None,
        base_uid=None,
        base_gid=None,
        principal=PrincipalRef(
            type="cloud_drive_user", identifier="dave@example.com"
        ),
    )
    assert perms.rights["read"].granted

    # An external email — no domain match → not granted.
    perms_external = compute_effective(
        acl=acl,
        base_mode=None,
        base_uid=None,
        base_gid=None,
        principal=PrincipalRef(
            type="cloud_drive_user", identifier="frank@other.com"
        ),
    )
    assert not perms_external.rights["read"].granted


def test_group_grant_matches_via_groups_list():
    acl = _acl([
        {
            "principal": {"type": "group", "id": "team-eng"},
            "role": "writer",
        }
    ])
    perms = compute_effective(
        acl=acl,
        base_mode=None,
        base_uid=None,
        base_gid=None,
        principal=PrincipalRef(
            type="cloud_drive_user", identifier="eve@example.com"
        ),
        groups=[GroupRef(type="cloud_drive_user", identifier="team-eng")],
    )
    assert perms.rights["write"].granted


# ---------------------------------------------------------------------------
# Denormalization to viewable_by_* tokens.
# ---------------------------------------------------------------------------


def test_denormalize_emits_user_token_with_email():
    acl = _acl([
        {
            "principal": {
                "type": "user",
                "id": "perm-alice",
                "email": "alice@example.com",
            },
            "role": "writer",
        }
    ])
    buckets = denormalize_acl(acl, base_mode=None, base_uid=None, base_gid=None)
    assert "cloud_drive:user:alice@example.com" in buckets["read"]
    assert "cloud_drive:user:alice@example.com" in buckets["write"]


def test_denormalize_anyone_falls_into_anyone_bucket():
    acl = _acl([
        {
            "principal": {"type": "anyone", "id": "anyone"},
            "role": "reader",
        }
    ])
    buckets = denormalize_acl(acl, base_mode=None, base_uid=None, base_gid=None)
    assert ANYONE in buckets["read"]


def test_denormalize_domain_emits_domain_token():
    acl = _acl([
        {
            "principal": {"type": "domain", "id": "example.com"},
            "role": "reader",
        }
    ])
    buckets = denormalize_acl(acl, base_mode=None, base_uid=None, base_gid=None)
    assert "cloud_drive:domain:example.com" in buckets["read"]


# ---------------------------------------------------------------------------
# Persistence — entries.native_id round-trip via the ingest router.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_native_id_persists_through_ingest(
    client: AsyncClient, db_session: AsyncSession
):
    user = User(
        id=uuid.uuid4(),
        username="nio",
        email="nio@x",
        password_hash="x",
        role="admin",
    )
    source = Source(
        id=uuid.uuid4(),
        name="drive-stub",
        type="local",  # any type — we're not running a real connector
        connection_config={"path": "/tmp"},
    )
    db_session.add_all([user, source])
    await db_session.commit()

    token = create_ingest_token(str(user.id))
    scan_id = await seed_scan(db_session, source.id)
    payload = {
        "source_id": str(source.id),
        "scan_id": str(scan_id),
        "is_final": True,
        "entries": [
            {
                "path": "/My Drive/Foo.docx",
                "name": "Foo.docx",
                "kind": "file",
                "size_bytes": 1024,
                "native_id": "1aBcD-providerOpaque",
            }
        ],
    }
    resp = await client.post(
        "/api/ingest/batch",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text

    result = await db_session.execute(
        select(Entry).where(Entry.source_id == source.id)
    )
    rows = list(result.scalars())
    assert len(rows) == 1
    assert rows[0].native_id == "1aBcD-providerOpaque"
