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
_FETCH_SEMAPHORE_LOOP: asyncio.AbstractEventLoop | None = None
PREVIEW_MAX_BYTES = 64 * 1024
_FETCH_CHUNK = 64 * 1024


def _semaphore() -> asyncio.Semaphore:
    """Lazily build the semaphore — and rebuild it if the running event
    loop changed (e.g., per-test loop in pytest-asyncio function scope).
    A semaphore is bound to the loop that creates it; reusing one across
    loops produces 'Future attached to a different loop' errors."""
    global _FETCH_SEMAPHORE, _FETCH_SEMAPHORE_LOOP
    loop = asyncio.get_running_loop()
    if _FETCH_SEMAPHORE is None or _FETCH_SEMAPHORE_LOOP is not loop:
        _FETCH_SEMAPHORE = asyncio.Semaphore(_FETCH_CONCURRENCY)
        _FETCH_SEMAPHORE_LOOP = loop
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

    Two checks must both pass:

      1. Lexical prefix: the normalized absolute path is under the root.
         Catches '..' traversal in the request itself.
      2. Symlink resolution: realpath of both root and candidate keep the
         candidate under root. Catches a symlink inside the source root
         that POINTS outside (e.g. /srv/data/escape -> /etc) — without
         this an attacker who can write a symlink into the indexed area
         could exfiltrate arbitrary files.
    """
    root = os.path.normpath(os.path.abspath(source_root))
    candidate = os.path.normpath(os.path.abspath(requested_path))

    # 1. Lexical check
    try:
        common = os.path.commonpath([root, candidate])
    except ValueError:
        raise PathTraversal(f"path {requested_path!r} not under {source_root!r}")
    if common != root:
        raise PathTraversal(f"path {requested_path!r} not under {source_root!r}")

    # 2. Symlink-escape check
    real_root = os.path.realpath(root)
    real_candidate = os.path.realpath(candidate)
    try:
        real_common = os.path.commonpath([real_root, real_candidate])
    except ValueError:
        raise PathTraversal(
            f"path {requested_path!r} resolves outside {source_root!r}"
        )
    if real_common != real_root:
        raise PathTraversal(
            f"path {requested_path!r} (symlink target) resolves outside {source_root!r}"
        )
    return candidate


def validate_remote_path(requested_path: str) -> str:
    """For non-local sources we don't have a host-side root to compare
    against, so the best we can do is reject obvious escape attempts.
    The remote server's own access controls are the real guard."""
    if not requested_path:
        raise PathTraversal("empty path")
    if "\x00" in requested_path:
        raise PathTraversal("path contains NUL byte")
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
        # Mirror source_tester's strict-host-key invariant: refuse to fetch
        # if known_hosts_path is missing on the source. The source-create
        # form requires it, so this only fails for sources created out of
        # band (direct DB insert, half-finished migration, etc.).
        kh = (cfg.get("known_hosts_path") or "").strip()
        if not kh:
            raise ContentFetchFailed(
                "config",
                "ssh source missing known_hosts_path; refusing to fetch",
            )
        argv += [
            "--host", cfg.get("host", ""),
            "--port", str(int(cfg.get("port") or 22)),
            "--user", cfg.get("username", ""),
            "--known-hosts", kh,
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


async def _drain_stderr(stream: asyncio.StreamReader) -> bytes:
    """Helper task that buffers stderr concurrently with stdout reads.
    Without this, stderr can fill its 64KB pipe buffer and block the
    scanner subprocess — a classic asyncio PIPE deadlock."""
    chunks: list[bytes] = []
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


async def stream_via_scanner(source, entry_path: str) -> AsyncIterator[bytes]:
    """Async-generator over the scanner subprocess's stdout. Bounded by the
    module-level concurrency semaphore.

    On non-zero exit, raises ContentFetchFailed with the step:reason from
    the scanner's stderr.

    On early close (caller breaks out of the iteration), the subprocess is
    killed promptly via `GeneratorExit` propagation: the outer `finally`
    block calls proc.kill() so we don't hold a slot on the concurrency
    semaphore waiting for a half-streamed scanner to finish on its own.
    """
    validate_remote_path(entry_path)
    argv, password, key_passphrase = _build_fetch_argv(source, entry_path)
    payload = stdin_creds_payload(password=password, key_passphrase=key_passphrase)

    sem = _semaphore()
    await sem.acquire()
    proc: asyncio.subprocess.Process | None = None
    stderr_task: asyncio.Task[bytes] | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Drain stderr concurrently so it never blocks on its kernel pipe
        # buffer. A scanner that's chatty on stderr while writing to stdout
        # would otherwise deadlock the entire fetch.
        assert proc.stderr is not None
        stderr_task = asyncio.create_task(_drain_stderr(proc.stderr))

        # Send creds and close stdin so the scanner's bufio.Scanner sees EOF.
        if proc.stdin is not None:
            proc.stdin.write(payload.encode())
            await proc.stdin.drain()
            proc.stdin.close()

        assert proc.stdout is not None
        try:
            while True:
                chunk = await proc.stdout.read(_FETCH_CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            # If the consumer broke early (GeneratorExit, exception), kill
            # the scanner so it doesn't hang waiting for the closed stdout.
            # On normal completion proc has already exited.
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            rc = await proc.wait()
            err = b""
            if stderr_task is not None:
                try:
                    err = await stderr_task
                except Exception:  # noqa: BLE001
                    pass
            if rc != 0:
                step, _, msg = err.decode(errors="replace").strip().partition(":")
                raise ContentFetchFailed(
                    step.strip() or "open",
                    msg.strip() or f"scanner exited {rc}",
                )
    finally:
        # Make sure the stderr task is cleaned up even on outer exceptions.
        if stderr_task is not None and not stderr_task.done():
            stderr_task.cancel()
            try:
                await stderr_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        sem.release()
