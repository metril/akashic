"""POST /api/sources/test — pre-flight connection probe for source creation.

Runs the same probe before save that the user gets when they click
"Test connection" in the UI. Records a `source_test_run` audit event
with the test result. Never logs or echoes back credentials in the
response payload.
"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.models.user import User
from akashic.services.audit import record_event
from akashic.services.source_tester import TestResult, test_connection

router = APIRouter(prefix="/api/sources", tags=["sources"])


class TestSourceRequest(BaseModel):
    type: str
    connection_config: dict


_AUDITABLE_KEYS = {
    "host", "port", "share", "domain", "bucket", "region",
    "endpoint", "username", "export_path", "path",
}


def _audit_payload(req: TestSourceRequest, result: TestResult) -> dict:
    """Return a payload safe for the audit log — copies non-sensitive
    config keys, never password/passphrase/secret_access_key."""
    cfg = req.connection_config or {}
    safe = {k: cfg.get(k) for k in _AUDITABLE_KEYS if k in cfg}
    return {
        "type": req.type,
        "config": safe,
        "ok": result.ok,
        "step": result.step,
        "error": result.error,
    }


@router.post("/test", response_model=TestResult)
async def post_test(
    body: TestSourceRequest,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = test_connection(body.type, body.connection_config)
    await record_event(
        db=db, user=user,
        event_type="source_test_run",
        payload=_audit_payload(body, result),
        request=request,
    )
    return result
