"""Fire-and-forget spawn of the bundled `akashic-scanner` for a triggered scan.

The trigger endpoint creates a Scan row in `pending` state but does NOT
itself execute the scan — that's this module. Without it, the scan record
sits there forever and no logs/heartbeats/batches ever arrive (which is
exactly the "I see no logs" complaint that prompted writing this).

Lifecycle:
  1. `/api/scans/trigger` returns immediately with the scan_id.
  2. FastAPI's BackgroundTasks invokes `spawn_scan(...)`.
  3. We build argv from the source type, mint a JWT scoped to the
     triggering user, and exec the scanner via asyncio.
  4. The scanner authenticates as that user against /api/scans/{id}/heartbeat
     and friends. Status flips through pending -> running -> completed
     (or cancelled / failed).
  5. We await the subprocess so any nonzero exit ends up in the api log.
     The watchdog separately catches scans that never start.

The user's JWT is given an extended expiry (24 h) so a long scan against
a slow share doesn't 401 mid-walk. This is a reasonable trade-off for a
trusted-LAN deployment; OIDC/LDAP-only setups should swap this for a
service-account token.
"""
from __future__ import annotations

import asyncio
import logging
import shlex
from datetime import timedelta
from typing import Any

from akashic.auth.jwt import create_access_token
from akashic.models.scan import Scan
from akashic.models.source import Source
from akashic.models.user import User
from akashic.services.scanner_helpers import scanner_binary_path

logger = logging.getLogger(__name__)


def _build_argv(binary: str, source: Source, scan: Scan) -> tuple[list[str], dict[str, str]]:
    """Translate Source.connection_config into scanner CLI args + env.

    Returns (argv, extra_env). Connection-secret values are passed via
    -pass on argv — same convention as the Phase B2 entry-content
    streamer. Treat hosts running this api as inside the trust boundary;
    /proc/<pid>/cmdline visibility is acceptable. Everything else
    (excludes, batch size) is non-secret.
    """
    cfg: dict[str, Any] = source.connection_config or {}
    argv: list[str] = [
        binary,
        "-source-id", str(source.id),
        "-scan-id", str(scan.id),
        "-type", source.type,
        "-batch-size", "1000",
    ]

    excludes = source.exclude_patterns or []
    if excludes:
        argv += ["-exclude", ",".join(excludes)]

    if scan.scan_type == "full":
        argv += ["-full"]

    # Per-type wiring. Keys mirror what AddSourceForm / SourceFieldSet
    # write into connection_config, plus the scanner's flag names.
    t = source.type
    if t == "local":
        argv += ["-root", str(cfg.get("path", ""))]
    elif t == "ssh":
        argv += [
            "-host", str(cfg.get("host", "")),
            "-port", str(cfg.get("port") or 22),
            "-user", str(cfg.get("username", "")),
            "-pass", str(cfg.get("password") or ""),
            "-known-hosts", str(cfg.get("known_hosts_path") or ""),
        ]
        if cfg.get("key_path"):
            argv += ["-key", str(cfg["key_path"])]
        if cfg.get("key_passphrase"):
            argv += ["-key-passphrase", str(cfg["key_passphrase"])]
        argv += ["-root", str(cfg.get("root_path") or "/")]
    elif t == "smb":
        argv += [
            "-host", str(cfg.get("host", "")),
            "-port", str(cfg.get("port") or 445),
            "-user", str(cfg.get("username", "")),
            "-pass", str(cfg.get("password") or ""),
            "-share", str(cfg.get("share", "")),
            "-root", str(cfg.get("root_path") or "."),
        ]
    elif t == "nfs":
        argv += [
            "-host", str(cfg.get("host", "")),
            "-port", str(cfg.get("port") or 2049),
            "-root", str(cfg.get("export_path") or "/"),
        ]
    elif t == "s3":
        argv += [
            "-bucket", str(cfg.get("bucket", "")),
            "-region", str(cfg.get("region", "us-east-1")),
            "-user", str(cfg.get("access_key_id") or ""),
            "-pass", str(cfg.get("secret_access_key") or ""),
            "-root", "/",
        ]
        if cfg.get("endpoint"):
            argv += ["-endpoint", str(cfg["endpoint"])]
    else:
        raise ValueError(f"unsupported source type: {t}")

    # Run the prewalk count pass when this is the source's first scan,
    # so the UI gets a real ETA on subsequent updates rather than
    # falling back to "unknown".
    if scan.previous_scan_files in (None, 0):
        argv += ["-prewalk"]

    return argv, {}


async def _await_proc(proc: asyncio.subprocess.Process, scan_id: str) -> None:
    """Background coroutine: wait for the scanner to exit, log on
    nonzero. Runs as a free-floating task so the caller doesn't block."""
    try:
        rc = await proc.wait()
        if rc != 0:
            logger.warning("scanner for scan_id=%s exited with rc=%d", scan_id, rc)
        else:
            logger.info("scanner for scan_id=%s exited cleanly", scan_id)
    except Exception:
        logger.exception("scanner wait for scan_id=%s failed", scan_id)


async def spawn_scan(source: Source, scan: Scan, user: User) -> None:
    """Spawn the akashic-scanner binary for `scan` and detach.

    Errors during spawn (binary missing, env unset, type unsupported)
    are caught and logged; the scan stays in `pending` and the watchdog
    will eventually fail it. We deliberately don't raise out of here —
    the trigger endpoint already returned 200 to the caller, and there's
    no good way to surface a post-response error.
    """
    binary = scanner_binary_path()
    if not binary:
        logger.error(
            "spawn_scan: akashic-scanner binary not on PATH (scan_id=%s)",
            scan.id,
        )
        return

    try:
        argv, extra_env = _build_argv(binary, source, scan)
    except Exception:
        logger.exception("spawn_scan: argv build failed (scan_id=%s)", scan.id)
        return

    # Long scans need a long-lived token. 24 h is well past any single
    # scan against a reasonable share; if a scan runs longer than that
    # in practice, the right fix is a service account, not a longer JWT.
    token = create_access_token(
        {"sub": str(user.id)},
        expires_delta=timedelta(hours=24),
    )
    env = {
        "AKASHIC_API_URL": "http://localhost:8000",
        "AKASHIC_API_KEY": token,
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        **extra_env,
    }

    logger.info(
        "spawn_scan: launching scanner scan_id=%s source_type=%s argv=%s",
        scan.id,
        source.type,
        # Truncate any password values for the log line — they're
        # already in /proc/<pid>/cmdline; no need to also tee them
        # through the api logs.
        shlex.join(_redact_for_log(argv)),
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            # Keep stderr connected so the scanner's stderr-relay can
            # tee it back through /api/scans/{id}/stderr. The relay
            # reopens fd 2 internally; what we set here is for the
            # parent's view, not the scanner's.
            stderr=asyncio.subprocess.DEVNULL,
        )
    except Exception:
        logger.exception("spawn_scan: exec failed (scan_id=%s)", scan.id)
        return

    asyncio.create_task(_await_proc(proc, str(scan.id)))


def _redact_for_log(argv: list[str]) -> list[str]:
    """Replace the value following any `-pass` / `-key-passphrase` flag
    with '<redacted>' so the api log line doesn't leak credentials."""
    out: list[str] = []
    redact_next = False
    for token in argv:
        if redact_next:
            out.append("<redacted>")
            redact_next = False
        else:
            out.append(token)
            if token in ("-pass", "-key-passphrase"):
                redact_next = True
    return out
