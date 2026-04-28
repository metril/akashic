import pytest

from akashic.models.audit_event import AuditEvent
from akashic.services.audit import record_event


@pytest.mark.asyncio
async def test_record_event_persists_minimal(db_session):
    from akashic.models.user import User
    user = User(username="alice", email="a@a", password_hash="x", role="user")
    db_session.add(user)
    await db_session.flush()

    await record_event(
        db=db_session,
        user=user,
        event_type="identity_added",
        payload={"fs_person_label": "My Work"},
        request=None,
    )
    await db_session.commit()

    from sqlalchemy import select
    rows = (await db_session.execute(select(AuditEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "identity_added"
    assert rows[0].user_id == user.id
    assert rows[0].payload == {"fs_person_label": "My Work"}
    assert rows[0].request_ip == ""
    assert rows[0].user_agent == ""


@pytest.mark.asyncio
async def test_record_event_captures_request_metadata(db_session):
    from akashic.models.user import User
    user = User(username="bob", email="b@b", password_hash="x", role="user")
    db_session.add(user)
    await db_session.flush()

    class _FakeRequest:
        client = type("c", (), {"host": "10.0.0.5"})()
        headers = {"user-agent": "curl/8.0"}

    await record_event(
        db=db_session,
        user=user,
        event_type="search_as_used",
        payload={"query": "foo", "results_count": 7},
        request=_FakeRequest(),
    )
    await db_session.commit()

    from sqlalchemy import select
    row = (await db_session.execute(select(AuditEvent))).scalar_one()
    assert row.request_ip == "10.0.0.5"
    assert row.user_agent == "curl/8.0"


@pytest.mark.asyncio
async def test_record_event_swallows_failures(db_session, caplog):
    """A broken db should NOT raise. Caller's user-facing op continues."""
    import logging
    caplog.set_level(logging.WARNING, logger="akashic.services.audit")

    class _BrokenSession:
        def add(self, _):
            raise RuntimeError("db on fire")

    # Should not raise.
    await record_event(
        db=_BrokenSession(),
        user=None,
        event_type="identity_added",
        payload={},
        request=None,
    )
    assert any("audit" in rec.message.lower() for rec in caplog.records)
