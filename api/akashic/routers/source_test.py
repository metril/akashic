"""POST /api/sources/test — pre-flight connection probe for source creation.

Runs the same probe before save that the user gets when they click
"Test connection" in the UI. Records a `source_test_run` audit event
with the test result. Never logs or echoes back credentials in the
response payload.
"""
import uuid

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
    # v0.31.3 — credential-profile-backed / host-attached sources. The
    # probe must be run with the profile's (encrypted) credentials and
    # the host's connection-level config merged in, exactly as a real
    # scan resolves them — otherwise an SMB source whose password lives
    # in a profile is always tested with no password and fails.
    credential_profile_id: uuid.UUID | None = None
    host_id: uuid.UUID | None = None


# Allow-list of connection_config keys that are safe to record in the audit
# log. Inverting the trust model: any new field that isn't here gets dropped
# rather than risk leaking a future credential. access_key_id is the public
# half of an AWS key pair — including it lets audit answer "which credentials
# were used" for S3 sources, which would otherwise have no identity field.
_AUDITABLE_KEYS = {
    "host", "port", "share", "domain", "bucket", "region",
    "endpoint", "username", "export_path", "path", "access_key_id",
    # Phase 3b — NFS AUTH_SYS identity. Operational, not secret.
    # Audit needs these to answer "which uid did the test run as?"
    "auth_uid", "auth_gid", "auth_aux_gids", "probe_timeout_seconds",
    # Phase 3c — Kerberos / RPCSEC_GSS. Principal/realm/SPN/keytab path
    # /config path are operational and useful for debugging "did the
    # test use the right service account?". The actual password and
    # keytab CONTENTS are never reachable here — _scrub_config strips
    # `krb5_password`, and `krb5_keytab_path` is a filesystem path, not
    # the keytab bytes.
    "auth_method",
    "krb5_principal", "krb5_realm", "krb5_service_principal",
    "krb5_keytab_path", "krb5_config_path",
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
    cfg = dict(body.connection_config or {})

    # v0.14.0 — OAuth-shaped sources (gdrive, …) at create time. The
    # source row doesn't exist yet, so mint_access_token_for_source
    # can't help. The form ships ``oauth_credential_id`` for the
    # not-yet-attached SourceOAuthCredential row; mint via that id.
    oauth_credential_id = cfg.pop("oauth_credential_id", None)
    if oauth_credential_id:
        from akashic.models.oauth_credential import SourceOAuthCredential
        from akashic.services.source_oauth import (
            OAuthExchangeFailed,
            mint_access_token,
        )
        cred = await db.get(SourceOAuthCredential, oauth_credential_id)
        if cred is not None:
            try:
                cfg["access_token"] = await mint_access_token(db, cred)
            except OAuthExchangeFailed as exc:
                result = TestResult(
                    ok=False, step="auth",
                    error=f"oauth refresh failed: {exc.detail[:200]}",
                )
                await record_event(
                    db=db, user=user,
                    event_type="source_test_run",
                    payload=_audit_payload(body, result),
                    request=request,
                )
                return result

    # v0.31.3 — layer credential-profile + host config UNDER the inline
    # config, same precedence as the scan-time merge_host_and_source:
    # host_profile < host_inline < source_profile < source_inline.
    if body.host_id is not None or body.credential_profile_id is not None:
        from akashic.models.credential_profile import CredentialProfile
        from akashic.models.host import Host
        from akashic.services.source_config import (
            _profile_credentials,
            credentials_from_profile,
        )
        merged: dict = {}
        if body.host_id is not None:
            host = await db.get(Host, body.host_id)
            if host is not None:
                merged.update(_profile_credentials(host))
                merged.update(host.connection_config or {})
        if body.credential_profile_id is not None:
            profile = await db.get(CredentialProfile, body.credential_profile_id)
            merged.update(credentials_from_profile(profile))
        merged.update(cfg)
        cfg = merged

    result = test_connection(body.type, cfg)
    await record_event(
        db=db, user=user,
        event_type="source_test_run",
        payload=_audit_payload(body, result),
        request=request,
    )
    return result
