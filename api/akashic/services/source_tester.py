"""Pre-flight connection tests for the source-creation form.

Local sources are checked directly (the API container has filesystem access
to whatever is mounted in). SSH/SMB/S3/NFS sources dispatch to the bundled
`akashic-scanner test-connection` subcommand, which speaks each protocol
natively and exits with a structured `step:reason` stderr line on failure.

NFS support is a TCP reachability probe against the NFS service port
(default 2049). It does not validate the export path — that would require
an ONC-RPC MOUNT or NFSv4 COMPOUND/LOOKUP client, which we don't have.
The probe still catches the common failure modes (wrong host, firewall,
server down).
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Literal, Optional

import httpx
from pydantic import BaseModel

from akashic.services.scanner_helpers import scanner_binary_path, stdin_creds_payload

logger = logging.getLogger(__name__)

Step = Literal["connect", "auth", "mount", "list", "config"]
_KNOWN_STEPS = ("connect", "auth", "mount", "list", "config")


class TestResult(BaseModel):
    ok: bool
    step: Optional[Step] = None
    error: Optional[str] = None
    # Phase 3a — for NFS, the cascade reports which protocol path
    # validated the export. UI surfaces this so users know whether the
    # success was strong (mount3 / nfsv4) or warning-level (tcp
    # fallback). None for non-NFS source types.
    tier: Optional[str] = None
    warn: Optional[str] = None


def _scanner_binary_path() -> str | None:
    """Test seam — wraps scanner_helpers.scanner_binary_path so existing
    monkeypatches keep working."""
    return scanner_binary_path()


def _run_scanner(
    argv: list[str],
    password: str = "",
    key_passphrase: str = "",
    krb5_password: str = "",
    timeout: int = 15,
) -> subprocess.CompletedProcess:
    """Synchronous run-and-collect for the short-lived test-connection probe.
    All credentials are fed via stdin JSON so they don't end up in
    /proc/<pid>/cmdline. The streaming entry-content path uses
    asyncio.create_subprocess_exec instead — see services/entry_content.py."""
    return subprocess.run(
        argv, capture_output=True, timeout=timeout, text=True,
        input=stdin_creds_payload(
            password=password,
            key_passphrase=key_passphrase,
            krb5_password=krb5_password,
        ),
    )


def _test_via_scanner(
    scanner_argv: list[str],
    password: str = "",
    key_passphrase: str = "",
    krb5_password: str = "",
    timeout: int = 15,
) -> TestResult:
    binary = _scanner_binary_path()
    if not binary:
        return TestResult(
            ok=False, step="config",
            error="akashic-scanner binary not found on PATH",
        )
    argv = [binary] + scanner_argv
    try:
        proc = _run_scanner(
            argv,
            password=password,
            key_passphrase=key_passphrase,
            krb5_password=krb5_password,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return TestResult(ok=False, step="connect", error="scanner timeout")
    except OSError as exc:
        return TestResult(ok=False, step="config", error=f"scanner spawn: {exc}")

    if proc.returncode == 0:
        # Parse the stdout JSON to capture optional `tier` / `warn`
        # fields the NFS path emits. If the JSON has unexpected shape
        # or missing fields, we still return ok=true; the UI can fall
        # back to a generic "Connection OK" without the tier breadcrumb.
        import json
        tier: str | None = None
        warn: str | None = None
        try:
            payload = json.loads((proc.stdout or "").strip() or "{}")
            if isinstance(payload, dict):
                t = payload.get("tier")
                if isinstance(t, str):
                    tier = t
                w = payload.get("warn")
                if isinstance(w, str):
                    warn = w
        except json.JSONDecodeError:
            pass
        return TestResult(ok=True, tier=tier, warn=warn)

    err = (proc.stderr or "").strip()
    step: Step | None = None
    if ":" in err:
        prefix, _, msg = err.partition(":")
        prefix = prefix.strip()
        if prefix in _KNOWN_STEPS:
            step = prefix  # type: ignore[assignment]
            err = msg.strip()
    return TestResult(ok=False, step=step, error=err)


def test_local(cfg: dict) -> TestResult:
    path = (cfg.get("path") or "").strip()
    if not path:
        return TestResult(ok=False, step="config", error="path required")
    if not os.path.isdir(path):
        return TestResult(ok=False, step="list", error=f"not a directory: {path}")
    if not os.access(path, os.R_OK):
        return TestResult(ok=False, step="list", error=f"not readable: {path}")
    return TestResult(ok=True)


def test_ssh(cfg: dict) -> TestResult:
    host = (cfg.get("host") or "").strip()
    user = (cfg.get("username") or "").strip()
    if not host or not user:
        return TestResult(ok=False, step="config", error="host and username required")
    if not (cfg.get("known_hosts_path") or "").strip():
        return TestResult(ok=False, step="config", error="known_hosts_path required")

    argv = [
        "test-connection", "--type=ssh",
        "--host", host,
        "--port", str(int(cfg.get("port") or 22)),
        "--user", user,
        "--known-hosts", cfg["known_hosts_path"],
        "--password-stdin",
    ]
    if cfg.get("key_path"):
        argv += ["--key", cfg["key_path"]]
    # key_passphrase is a credential — pipe via stdin alongside password so it
    # doesn't end up in /proc/<pid>/cmdline.
    return _test_via_scanner(
        argv,
        password=cfg.get("password") or "",
        key_passphrase=cfg.get("key_passphrase") or "",
    )


def test_smb(cfg: dict) -> TestResult:
    host = (cfg.get("host") or "").strip()
    user = (cfg.get("username") or "").strip()
    share = (cfg.get("share") or "").strip()
    if not host or not user or not share:
        return TestResult(
            ok=False, step="config",
            error="host, username, share required",
        )
    argv = [
        "test-connection", "--type=smb",
        "--host", host,
        "--port", str(int(cfg.get("port") or 445)),
        "--user", user,
        "--share", share,
        "--password-stdin",
    ]
    return _test_via_scanner(argv, password=cfg.get("password") or "")


def test_s3(cfg: dict) -> TestResult:
    bucket = (cfg.get("bucket") or "").strip()
    region = (cfg.get("region") or "").strip()
    if not bucket or not region:
        return TestResult(
            ok=False, step="config", error="bucket and region required",
        )
    argv = [
        "test-connection", "--type=s3",
        "--bucket", bucket,
        "--region", region,
        "--password-stdin",
    ]
    if cfg.get("endpoint"):
        argv += ["--endpoint", cfg["endpoint"]]
    if cfg.get("access_key_id"):
        argv += ["--user", cfg["access_key_id"]]
    # v0.8.1 — forward path_style override when present so the test
    # path matches what a real scan would do. Auto (omit flag) keeps
    # the legacy behaviour for AWS / MinIO; explicit true/false is
    # the Wasabi/B2/proxy-rewrite escape hatch.
    if "path_style" in cfg:
        v = cfg.get("path_style")
        if isinstance(v, bool):
            argv += ["--path-style", "true" if v else "false"]
        elif isinstance(v, str) and v.strip().lower() in ("true", "false", "0", "1", "yes", "no"):
            argv += ["--path-style", v.strip().lower()]
    return _test_via_scanner(argv, password=cfg.get("secret_access_key") or "")


def test_nfs(cfg: dict) -> TestResult:
    host = (cfg.get("host") or "").strip()
    export_path = (cfg.get("export_path") or "").strip()
    if not host or not export_path:
        return TestResult(
            ok=False, step="config", error="host and export_path required",
        )

    # AUTH_SYS uid/gid plumbing — Phase 3b. Defaults match the probe's
    # built-in defaults (uid 0 / gid 0 / no aux gids), so an unset
    # source_config behaves exactly like Phase 3a.
    try:
        auth_uid = int(cfg.get("auth_uid", 0) or 0)
        auth_gid = int(cfg.get("auth_gid", 0) or 0)
    except (TypeError, ValueError):
        return TestResult(
            ok=False, step="config",
            error="auth_uid and auth_gid must be integers",
        )
    raw_aux = cfg.get("auth_aux_gids")
    if isinstance(raw_aux, list):
        try:
            aux_str = ",".join(str(int(x)) for x in raw_aux if str(x).strip())
        except (TypeError, ValueError):
            return TestResult(
                ok=False, step="config",
                error="auth_aux_gids must be a list of integers",
            )
    elif isinstance(raw_aux, str):
        # Validate string-form input the same way as list form so a
        # form-side bug or hand-built API call can't smuggle non-integer
        # fragments through the silent-drop in the scanner.
        parts = [p.strip() for p in raw_aux.split(",") if p.strip()]
        try:
            aux_str = ",".join(str(int(p)) for p in parts)
        except ValueError:
            return TestResult(
                ok=False, step="config",
                error="auth_aux_gids must be a comma-separated list of integers",
            )
    else:
        aux_str = ""

    raw_timeout = cfg.get("probe_timeout_seconds", 0)
    try:
        timeout_seconds = int(raw_timeout or 0)
    except (TypeError, ValueError):
        return TestResult(
            ok=False, step="config",
            error="probe_timeout_seconds must be an integer",
        )
    if timeout_seconds and (timeout_seconds < 1 or timeout_seconds > 60):
        return TestResult(
            ok=False, step="config",
            error="probe_timeout_seconds must be between 1 and 60",
        )

    # Phase 3c — kerberos / RPCSEC_GSS. auth_method=sys is the default;
    # krb5 takes a principal+realm and either a keytab path or a password.
    # krb5i/krb5p surface as scanner-side config errors — they're declared
    # in the schema for forward-compat but the probe rejects them.
    auth_method = (cfg.get("auth_method") or "sys").strip().lower()
    if auth_method not in {"sys", "krb5", "krb5i", "krb5p"}:
        return TestResult(
            ok=False, step="config",
            error=f"auth_method must be one of sys / krb5 / krb5i / krb5p (got {auth_method!r})",
        )

    krb5_principal = ""
    krb5_realm = ""
    krb5_spn = ""
    krb5_keytab_path = ""
    krb5_config_path = ""
    krb5_password = ""
    if auth_method != "sys":
        krb5_principal = (cfg.get("krb5_principal") or "").strip()
        krb5_realm = (cfg.get("krb5_realm") or "").strip()
        krb5_spn = (cfg.get("krb5_service_principal") or "").strip()
        krb5_keytab_path = (cfg.get("krb5_keytab_path") or "").strip()
        krb5_config_path = (cfg.get("krb5_config_path") or "").strip()
        krb5_password = cfg.get("krb5_password") or ""
        if not krb5_principal or not krb5_realm:
            return TestResult(
                ok=False, step="config",
                error="krb5 auth requires krb5_principal and krb5_realm",
            )
        if not krb5_keytab_path and not krb5_password:
            return TestResult(
                ok=False, step="config",
                error="krb5 auth requires krb5_keytab_path or krb5_password",
            )
        if krb5_keytab_path and krb5_password:
            return TestResult(
                ok=False, step="config",
                error="krb5_keytab_path and krb5_password are mutually exclusive",
            )

    argv = [
        "test-connection", "--type=nfs",
        "--host", host,
        "--port", str(int(cfg.get("port") or 2049)),
        "--export-path", export_path,
        "--auth-uid", str(auth_uid),
        "--auth-gid", str(auth_gid),
        "--auth-method", auth_method,
        "--password-stdin",
    ]
    if aux_str:
        argv += ["--auth-aux-gids", aux_str]
    if timeout_seconds:
        argv += ["--timeout", str(timeout_seconds)]
    if auth_method != "sys":
        argv += ["--krb5-principal", krb5_principal, "--krb5-realm", krb5_realm]
        if krb5_spn:
            argv += ["--krb5-service-principal", krb5_spn]
        if krb5_keytab_path:
            argv += ["--krb5-keytab", krb5_keytab_path]
        if krb5_config_path:
            argv += ["--krb5-config", krb5_config_path]

    # Subprocess timeout matches the scanner's own outer context. For
    # AUTH_SYS that's 3× per-RPC (3 round trips: portmap, MNT, UMNT).
    # For krb5 the scanner gives itself 5× (KDC AS_REQ, KDC TGS_REQ,
    # NFS GSS_INIT, NFS LOOKUP, plus retries). Plus a small reap margin
    # so the API doesn't kill the scanner before its own deadline expires.
    per_rpc = timeout_seconds or 5
    multiplier = 5 if auth_method != "sys" else 3
    sub_timeout = per_rpc * multiplier + 5
    return _test_via_scanner(argv, timeout=sub_timeout, krb5_password=krb5_password)


def test_paperless(cfg: dict) -> TestResult:
    """v0.7.0 — Tier 3 self-hosted library probe.

    Validates that the URL responds, the API token is accepted, and
    the documents endpoint serves a page. Inline httpx call (not a
    scanner subprocess) because the api container is closer to the
    paperless instance than the scanner is for most deployments and
    we don't need the agent's special process isolation here.

    Auth-rejected (401/403) → step="auth"; non-2xx → step="list";
    transport / TLS / DNS errors → step="connect".
    """
    raw_url = (cfg.get("url") or "").strip()
    api_token = (cfg.get("api_token") or "").strip()
    if not raw_url:
        return TestResult(ok=False, step="config", error="url required")
    if not api_token:
        return TestResult(ok=False, step="config", error="api_token required")
    base = raw_url.rstrip("/")
    target = f"{base}/api/documents/?page_size=1"
    verify = cfg.get("tls_verify")
    # Default to True; allow explicit `false` to skip cert validation
    # for self-signed installs. The boolean lands as a real bool when
    # round-tripped through Pydantic; tolerate stringy values from
    # the legacy connection_config blob.
    if verify is None:
        verify = True
    elif isinstance(verify, str):
        verify = verify.strip().lower() not in ("false", "0", "no")
    headers = {"Authorization": f"Token {api_token}", "Accept": "application/json"}
    try:
        with httpx.Client(timeout=10.0, verify=bool(verify)) as client:
            resp = client.get(target, headers=headers)
    except httpx.RequestError as exc:
        return TestResult(ok=False, step="connect", error=str(exc))
    if resp.status_code in (401, 403):
        return TestResult(
            ok=False, step="auth",
            error=f"authentication rejected ({resp.status_code})",
        )
    if resp.status_code >= 400:
        body = resp.text[:200]
        return TestResult(
            ok=False, step="list",
            error=f"GET /api/documents/ returned {resp.status_code}: {body}",
        )
    return TestResult(ok=True)


def test_azureblob(cfg: dict) -> TestResult:
    """v0.9.0 — Tier 2 PR 2 probe (Azure Blob Storage).

    Subprocesses the scanner CLI's `test-connection --type=azureblob`
    so the same auth chain (Shared Key / SAS / DefaultAzureCredential)
    that a production scan uses is exercised on the Test button. The
    secret (account_key or sas_token) is piped over stdin via the
    existing `--password-stdin` plumbing so it never appears in
    `/proc/<pid>/cmdline`.
    """
    account = (cfg.get("account_name") or "").strip()
    container = (cfg.get("container") or "").strip()
    if not account or not container:
        return TestResult(
            ok=False, step="config", error="account_name and container required",
        )
    auth_mode = (cfg.get("auth_mode") or "").strip().lower()
    if auth_mode not in ("", "account_key", "sas_token", "azure_ad"):
        return TestResult(
            ok=False, step="config",
            error=f"invalid auth_mode {auth_mode!r}",
        )
    # Pick the credential matching the chosen auth mode. Empty
    # auth_mode falls back the same way the connector does: account_key
    # if account_key is set, sas_token if sas_token is set, else
    # azure_ad (no inline secret needed).
    secret = ""
    if auth_mode == "account_key" or (auth_mode == "" and (cfg.get("account_key") or "").strip()):
        secret = cfg.get("account_key") or ""
    elif auth_mode == "sas_token" or (auth_mode == "" and (cfg.get("sas_token") or "").strip()):
        secret = cfg.get("sas_token") or ""
    argv = [
        "test-connection", "--type=azureblob",
        "--account-name", account,
        "--container", container,
        "--password-stdin",
    ]
    if auth_mode:
        argv += ["--auth-mode", auth_mode]
    if cfg.get("endpoint_suffix"):
        argv += ["--endpoint-suffix", cfg["endpoint_suffix"]]
    return _test_via_scanner(argv, password=secret)


def test_webdav(cfg: dict) -> TestResult:
    """v0.11.0 — Tier 4 PR 1 probe (WebDAV).

    Inline httpx PROPFIND against the source URL. The api container
    is closer to most self-hosted WebDAV instances than the scanner
    is, and the protocol is plain HTTP, so the round trip via the
    scanner subprocess would just add latency without any signal
    the inline probe doesn't already give.
    """
    raw_url = (cfg.get("url") or "").strip()
    username = cfg.get("username") or ""
    password = cfg.get("password") or ""
    if not raw_url:
        return TestResult(ok=False, step="config", error="url required")
    base = raw_url.rstrip("/") + "/"
    verify = cfg.get("tls_verify")
    if verify is None:
        verify = True
    elif isinstance(verify, str):
        verify = verify.strip().lower() not in ("false", "0", "no")
    propfind_body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<D:propfind xmlns:D="DAV:"><D:prop><D:resourcetype/></D:prop></D:propfind>'
    )
    headers = {
        "Depth": "0",
        "Content-Type": "application/xml; charset=utf-8",
    }
    try:
        with httpx.Client(timeout=10.0, verify=bool(verify)) as client:
            resp = client.request(
                "PROPFIND",
                base,
                headers=headers,
                content=propfind_body,
                auth=(username, password) if (username or password) else None,
            )
    except httpx.RequestError as exc:
        return TestResult(ok=False, step="connect", error=str(exc))
    if resp.status_code in (401, 403):
        return TestResult(
            ok=False, step="auth",
            error=f"authentication rejected ({resp.status_code})",
        )
    if resp.status_code == 405:
        return TestResult(
            ok=False, step="list",
            error="PROPFIND not allowed — server may not speak WebDAV at this URL",
        )
    if resp.status_code == 404:
        return TestResult(
            ok=False, step="list",
            error=f"resource not found ({resp.status_code})",
        )
    if resp.status_code not in (200, 207):
        body = resp.text[:200]
        return TestResult(
            ok=False, step="list",
            error=f"PROPFIND returned {resp.status_code}: {body}",
        )
    return TestResult(ok=True)


def test_gcs(cfg: dict) -> TestResult:
    """v0.10.0 — Tier 2 PR 3 probe (Google Cloud Storage).

    Subprocesses the scanner CLI's `test-connection --type=gcs`. The
    service account JSON key (when service_account_json mode is
    chosen) goes over stdin via `--password-stdin` so the multi-KB
    JSON payload doesn't bloat /proc/<pid>/cmdline. application_default
    mode skips the secret entirely; the scanner picks up workload
    identity at probe time.
    """
    bucket = (cfg.get("bucket") or "").strip()
    if not bucket:
        return TestResult(ok=False, step="config", error="bucket required")
    auth_mode = (cfg.get("auth_mode") or "").strip().lower()
    if auth_mode not in ("", "service_account_json", "application_default"):
        return TestResult(
            ok=False, step="config",
            error=f"invalid auth_mode {auth_mode!r}",
        )
    secret = ""
    if auth_mode == "service_account_json" or (
        auth_mode == "" and (cfg.get("service_account_json") or "").strip()
    ):
        secret = cfg.get("service_account_json") or ""
    argv = [
        "test-connection", "--type=gcs",
        "--bucket", bucket,
        "--password-stdin",
    ]
    if auth_mode:
        argv += ["--auth-mode", auth_mode]
    if cfg.get("prefix"):
        argv += ["--gcs-prefix", cfg["prefix"]]
    return _test_via_scanner(argv, password=secret)


def test_immich(cfg: dict) -> TestResult:
    """v0.8.0 — Tier 3 self-hosted library probe (photos / videos).

    Validates URL + api_key by hitting `/api/server-info/ping`. Same
    classification as test_paperless: 401/403 → auth, non-2xx → list,
    transport → connect. The api container is closer to most Immich
    instances than the scanner, so this stays an inline httpx call
    rather than a scanner subprocess.
    """
    raw_url = (cfg.get("url") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    if not raw_url:
        return TestResult(ok=False, step="config", error="url required")
    if not api_key:
        return TestResult(ok=False, step="config", error="api_key required")
    base = raw_url.rstrip("/")
    target = f"{base}/api/server-info/ping"
    verify = cfg.get("tls_verify")
    if verify is None:
        verify = True
    elif isinstance(verify, str):
        verify = verify.strip().lower() not in ("false", "0", "no")
    headers = {"x-api-key": api_key, "Accept": "application/json"}
    try:
        with httpx.Client(timeout=10.0, verify=bool(verify)) as client:
            resp = client.get(target, headers=headers)
    except httpx.RequestError as exc:
        return TestResult(ok=False, step="connect", error=str(exc))
    if resp.status_code in (401, 403):
        return TestResult(
            ok=False, step="auth",
            error=f"authentication rejected ({resp.status_code})",
        )
    if resp.status_code >= 400:
        body = resp.text[:200]
        return TestResult(
            ok=False, step="list",
            error=f"GET /api/server-info/ping returned {resp.status_code}: {body}",
        )
    return TestResult(ok=True)


_DISPATCH = {
    "local":     test_local,
    "ssh":       test_ssh,
    "smb":       test_smb,
    "s3":        test_s3,
    "nfs":       test_nfs,
    "paperless": test_paperless,
    "immich":    test_immich,
    "azureblob": test_azureblob,
    "gcs":       test_gcs,
    "webdav":    test_webdav,
}


def test_connection(source_type: str, connection_config: dict) -> TestResult:
    fn = _DISPATCH.get(source_type)
    if fn is None:
        return TestResult(
            ok=False, step="config",
            error=f"unsupported source type: {source_type!r}",
        )
    try:
        return fn(connection_config or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("source-test (%s) raised: %s", source_type, exc)
        return TestResult(ok=False, step="config", error=str(exc))
