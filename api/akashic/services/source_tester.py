"""Pre-flight connection tests for the source-creation form.

Local sources are checked directly (the API container has filesystem access
to whatever is mounted in). SSH/SMB/S3 sources dispatch to the bundled
`akashic-scanner test-connection` subcommand, which speaks each protocol
natively and exits with a structured `step:reason` stderr line on failure.

NFS test is intentionally not implemented yet (Phase B1.1) — saves still
work, the user just doesn't get pre-flight validation.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from typing import Literal, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

Step = Literal["connect", "auth", "mount", "list", "config"]
_KNOWN_STEPS = ("connect", "auth", "mount", "list", "config")


class TestResult(BaseModel):
    ok: bool
    step: Optional[Step] = None
    error: Optional[str] = None


_SCANNER_BIN_ENV = "AKASHIC_SCANNER_BIN"


def _scanner_binary_path() -> str | None:
    """Returns the akashic-scanner binary path, or None if not findable.
    Tests can monkeypatch this to inject a fake."""
    p = os.environ.get(_SCANNER_BIN_ENV)
    if p and os.path.isfile(p):
        return p
    return shutil.which("akashic-scanner")


def _run_scanner(argv: list[str], password: str = "", timeout: int = 15) -> subprocess.CompletedProcess:
    """Indirection for tests to monkeypatch.

    Password is fed via stdin JSON so it doesn't show up in /proc/<pid>/cmdline.
    """
    payload = json.dumps({"password": password}) + "\n"
    return subprocess.run(
        argv, capture_output=True, timeout=timeout, text=True, input=payload,
    )


def _test_via_scanner(scanner_argv: list[str], password: str = "") -> TestResult:
    binary = _scanner_binary_path()
    if not binary:
        return TestResult(
            ok=False, step="config",
            error="akashic-scanner binary not found on PATH",
        )
    argv = [binary] + scanner_argv
    try:
        proc = _run_scanner(argv, password=password)
    except subprocess.TimeoutExpired:
        return TestResult(ok=False, step="connect", error="scanner timeout")
    except OSError as exc:
        return TestResult(ok=False, step="config", error=f"scanner spawn: {exc}")

    if proc.returncode == 0:
        return TestResult(ok=True)

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
        if cfg.get("key_passphrase"):
            argv += ["--key-passphrase", cfg["key_passphrase"]]
    return _test_via_scanner(argv, password=cfg.get("password") or "")


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
    return _test_via_scanner(argv, password=cfg.get("secret_access_key") or "")


def test_nfs(cfg: dict) -> TestResult:
    if not (cfg.get("host") or "").strip() or not (cfg.get("export_path") or "").strip():
        return TestResult(
            ok=False, step="config", error="host and export_path required",
        )
    return TestResult(
        ok=False, step="config",
        error="NFS connection test not yet supported (saves still work)",
    )


_DISPATCH = {
    "local": test_local,
    "ssh":   test_ssh,
    "smb":   test_smb,
    "s3":    test_s3,
    "nfs":   test_nfs,
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
