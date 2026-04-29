"""File-content streaming for /api/entries/{id}/content and /preview.

Local sources are read directly from the API container's filesystem
(via FileResponse for the content endpoint, raw read for preview).
Non-local sources spawn `akashic-scanner fetch` and stream stdout.

Concurrency: a module-level asyncio.Semaphore caps the number of
simultaneously-running scanner subprocesses (default 10) so a request
flood doesn't fork unbounded subprocess. The cap is per-API-process.
"""
from __future__ import annotations

import asyncio
import logging
import os
import os.path
from typing import AsyncIterator, Optional

from akashic.services.scanner_helpers import scanner_binary_path, stdin_creds_payload

logger = logging.getLogger(__name__)

# Cap on simultaneous scanner subprocesses (per api process). Default 10
# matches the spec; override via env var for stress tests.
_FETCH_CONCURRENCY = int(os.environ.get("AKASHIC_FETCH_CONCURRENCY", "10"))
_FETCH_SEMAPHORE: asyncio.Semaphore | None = None
PREVIEW_MAX_BYTES = 64 * 1024
_FETCH_CHUNK = 64 * 1024


def _semaphore() -> asyncio.Semaphore:
    """Lazily build the semaphore so it binds to the running event loop."""
    global _FETCH_SEMAPHORE
    if _FETCH_SEMAPHORE is None:
        _FETCH_SEMAPHORE = asyncio.Semaphore(_FETCH_CONCURRENCY)
    return _FETCH_SEMAPHORE


class ContentFetchFailed(Exception):
    """Raised when the scanner subprocess fails or path validation rejects."""

    def __init__(self, step: str, message: str):
        super().__init__(f"{step}: {message}")
        self.step = step
        self.message = message


class PathTraversal(Exception):
    """Raised when the requested entry.path escapes the source root."""


# ── Path validation ────────────────────────────────────────────────────────


def validate_local_path(source_root: str, requested_path: str) -> str:
    """Returns the canonical absolute path inside source_root, or raises
    PathTraversal if requested_path resolves outside it.

    Important: we DON'T resolve symlinks (no realpath) because a
    sanctioned source root may legitimately contain symlinks the user
    expects to follow. We only normalize . and .. components.
    """
    root = os.path.normpath(os.path.abspath(source_root))
    candidate = os.path.normpath(os.path.abspath(requested_path))
    # commonpath rejects mismatched-drive paths cleanly on Linux this is
    # just a prefix check.
    try:
        common = os.path.commonpath([root, candidate])
    except ValueError:
        raise PathTraversal(f"path {requested_path!r} not under {source_root!r}")
    if common != root:
        raise PathTraversal(f"path {requested_path!r} not under {source_root!r}")
    return candidate


def validate_remote_path(requested_path: str) -> str:
    """For non-local sources we don't have a host-side root to compare
    against, so the best we can do is reject obvious escape attempts.
    The remote server's own access controls are the real guard."""
    if not requested_path:
        raise PathTraversal("empty path")
    parts = requested_path.replace("\\", "/").split("/")
    if any(p == ".." for p in parts):
        raise PathTraversal(f"path contains '..': {requested_path!r}")
    return requested_path


# ── Local read ─────────────────────────────────────────────────────────────


def open_local(source_root: str, entry_path: str) -> tuple[str, int]:
    """Returns (canonical_path, total_size). Raises:
       - PathTraversal if the path escapes source_root.
       - FileNotFoundError if the file is gone.
       - PermissionError if not readable."""
    canon = validate_local_path(source_root, entry_path)
    if not os.path.isfile(canon):
        raise FileNotFoundError(canon)
    if not os.access(canon, os.R_OK):
        raise PermissionError(canon)
    return canon, os.path.getsize(canon)


def read_local_preview(canonical_path: str, max_bytes: int = PREVIEW_MAX_BYTES) -> bytes:
    with open(canonical_path, "rb") as f:
        return f.read(max_bytes)


# ── Scanner-streamed read (non-local sources) ──────────────────────────────


def _build_fetch_argv(source, entry_path: str) -> tuple[list[str], str, str]:
    """Returns (argv, password, key_passphrase) for spawning the scanner.
    Raises ContentFetchFailed if the binary isn't on PATH."""
    binary = scanner_binary_path()
    if not binary:
        raise ContentFetchFailed("config", "akashic-scanner binary not found on PATH")

    cfg = source.connection_config or {}
    argv = [
        binary, "fetch",
        "--type", source.type,
        "--path", entry_path,
        "--password-stdin",
    ]
    if source.type == "ssh":
        argv += [
            "--host", cfg.get("host", ""),
            "--port", str(int(cfg.get("port") or 22)),
            "--user", cfg.get("username", ""),
            "--known-hosts", cfg.get("known_hosts_path", ""),
        ]
        if cfg.get("key_path"):
            argv += ["--key", cfg["key_path"]]
    elif source.type == "smb":
        argv += [
            "--host", cfg.get("host", ""),
            "--port", str(int(cfg.get("port") or 445)),
            "--user", cfg.get("username", ""),
            "--share", cfg.get("share", ""),
        ]
    elif source.type == "s3":
        argv += [
            "--bucket", cfg.get("bucket", ""),
            "--region", cfg.get("region", ""),
        ]
        if cfg.get("endpoint"):
            argv += ["--endpoint", cfg["endpoint"]]
        if cfg.get("access_key_id"):
            argv += ["--user", cfg["access_key_id"]]

    password = cfg.get("password") or cfg.get("secret_access_key") or ""
    key_passphrase = cfg.get("key_passphrase") or ""
    return argv, password, key_passphrase


async def stream_via_scanner(source, entry_path: str) -> AsyncIterator[bytes]:
    """Async-generator over the scanner subprocess's stdout. Bounded by the
    module-level concurrency semaphore.

    On non-zero exit, raises ContentFetchFailed with the step:reason from
    the scanner's stderr.
    """
    validate_remote_path(entry_path)
    argv, password, key_passphrase = _build_fetch_argv(source, entry_path)
    payload = stdin_creds_payload(password=password, key_passphrase=key_passphrase)

    sem = _semaphore()
    await sem.acquire()
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Send creds and close stdin so the scanner's bufio.Scanner sees EOF.
        if proc.stdin is not None:
            proc.stdin.write(payload.encode())
            await proc.stdin.drain()
            proc.stdin.close()
        try:
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(_FETCH_CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            rc = await proc.wait()
            if rc != 0:
                err = b""
                if proc.stderr is not None:
                    err = await proc.stderr.read()
                step, _, msg = err.decode(errors="replace").strip().partition(":")
                raise ContentFetchFailed(
                    step.strip() or "open",
                    msg.strip() or f"scanner exited {rc}",
                )
    finally:
        sem.release()
