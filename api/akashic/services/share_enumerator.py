"""Per-type share/export/bucket enumeration via the bundled
`akashic-scanner list-shares` subcommand.

Mirrors the shape of services/source_tester.py — same scanner-binary
discovery, same stdin-piped credential JSON convention, same
`step:reason` stderr classification — but the success payload is a
`{"shares": [...]}` dict instead of `{"ok": true}`.

Used by POST /api/hosts/{id}/list-shares to populate the Discover
Shares panel on the Hosts page.
"""
from __future__ import annotations

import json
import logging
import subprocess
from typing import Literal, Optional

from pydantic import BaseModel, Field

from akashic.services.scanner_helpers import scanner_binary_path, stdin_creds_payload
from akashic.services.source_tester import Step

# Mirror source_tester._KNOWN_STEPS — kept distinct (rather than
# imported) so the share-enumerator can ship without a private-name
# cross-module dependency.
_KNOWN_STEPS = ("connect", "auth", "mount", "list", "config")

logger = logging.getLogger(__name__)


# Host types `list-shares` knows how to enumerate. SSH and `local`
# return 400 from the API endpoint — there's no shares concept for
# either. Kept distinct from source-tester's broader set.
SUPPORTED_TYPES: frozenset[str] = frozenset({"smb", "nfs", "s3"})


class ListSharesResult(BaseModel):
    """Probe outcome shape returned to the API endpoint.

    `shares` is empty when `step` is set; non-empty (possibly zero)
    when the probe succeeded — an empty list means the host is
    reachable and authenticated but really has no shares to offer.
    """

    shares: list[str] = Field(default_factory=list)
    step: Optional[Step] = None
    error: Optional[str] = None


def _run_scanner(
    argv: list[str],
    *,
    password: str = "",
    timeout: int = 15,
) -> subprocess.CompletedProcess:
    """Synchronous run for the short-lived list-shares probe. All
    credentials piped via stdin to keep them out of /proc/cmdline."""
    return subprocess.run(
        argv, capture_output=True, timeout=timeout, text=True,
        input=stdin_creds_payload(password=password),
    )


def _list_via_scanner(
    scanner_argv: list[str],
    *,
    password: str = "",
    timeout: int = 15,
) -> ListSharesResult:
    binary = scanner_binary_path()
    if not binary:
        return ListSharesResult(
            step="config", error="akashic-scanner binary not found on PATH",
        )
    argv = [binary] + scanner_argv
    try:
        proc = _run_scanner(argv, password=password, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ListSharesResult(step="connect", error="scanner timeout")
    except OSError as exc:
        return ListSharesResult(step="config", error=f"scanner spawn: {exc}")

    if proc.returncode == 0:
        try:
            payload = json.loads((proc.stdout or "").strip() or "{}")
        except json.JSONDecodeError as exc:
            return ListSharesResult(step="list", error=f"bad scanner output: {exc}")
        raw = payload.get("shares") if isinstance(payload, dict) else None
        if not isinstance(raw, list):
            return ListSharesResult(step="list", error="scanner returned no shares list")
        out = [str(s) for s in raw if isinstance(s, str)]
        return ListSharesResult(shares=out)

    err = (proc.stderr or "").strip()
    step: Step | None = None
    if ":" in err:
        prefix, _, msg = err.partition(":")
        prefix = prefix.strip()
        if prefix in _KNOWN_STEPS:
            step = prefix  # type: ignore[assignment]
            err = msg.strip()
    return ListSharesResult(step=step, error=err)


def list_smb(cfg: dict) -> ListSharesResult:
    host = (cfg.get("host") or "").strip()
    user = (cfg.get("username") or "").strip()
    if not host or not user:
        return ListSharesResult(
            step="config", error="host and username required",
        )
    argv = [
        "list-shares", "--type=smb",
        "--host", host,
        "--port", str(int(cfg.get("port") or 445)),
        "--user", user,
        "--password-stdin",
    ]
    return _list_via_scanner(argv, password=cfg.get("password") or "")


def list_nfs(cfg: dict) -> ListSharesResult:
    host = (cfg.get("host") or "").strip()
    if not host:
        return ListSharesResult(step="config", error="host required")
    argv = [
        "list-shares", "--type=nfs",
        "--host", host,
        # Port 0 = portmap-discover mountd. Don't honour the cfg port
        # here because that's the NFS data port (2049), not mountd.
        "--port", "0",
    ]
    return _list_via_scanner(argv, timeout=20)


def list_s3(cfg: dict) -> ListSharesResult:
    region = (cfg.get("region") or "").strip()
    access_key = (cfg.get("access_key_id") or "").strip()
    if not region or not access_key:
        return ListSharesResult(
            step="config", error="region and access_key_id required",
        )
    argv = [
        "list-shares", "--type=s3",
        "--region", region,
        "--user", access_key,
        "--password-stdin",
    ]
    if cfg.get("endpoint"):
        argv += ["--endpoint", cfg["endpoint"]]
    return _list_via_scanner(argv, password=cfg.get("secret_access_key") or "")


_DISPATCH = {
    "smb": list_smb,
    "nfs": list_nfs,
    "s3":  list_s3,
}


def list_shares(host_type: str, connection_config: dict) -> ListSharesResult:
    """Dispatch to the per-type lister. Caller must guard against
    `local` at the endpoint level (returns config error here
    if it slips through)."""
    fn = _DISPATCH.get(host_type)
    if fn is None:
        return ListSharesResult(
            step="config",
            error=f"list-shares does not support type {host_type!r}",
        )
    try:
        return fn(connection_config or {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("share-enumerator (%s) raised: %s", host_type, exc)
        return ListSharesResult(step="config", error=str(exc))
