"""Spawn the bundled `akashic-scanner delete` subprocess to remove a
single file from a source. Used by the bulk-delete-copies endpoint in
the Duplicates flow.

This is the destructive twin of services/entry_content.py: that one
streams reads, this one performs writes. Both share the same argv
shape, the same stdin-creds JSON, and the same step:reason error
classification.

Why a subprocess at all? The scanner connectors are Go and live in the
binary; the api process can't import them directly. The subprocess
boundary also means a wedged smb client won't take down the api.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

from akashic.services.scanner_helpers import scanner_binary_path, stdin_creds_payload
from akashic.services.entry_content import validate_local_path, validate_remote_path, PathTraversal

logger = logging.getLogger(__name__)

# Per-call timeout. A delete should complete in well under a second on a
# healthy share; anything beyond ~30s is almost certainly a hung
# connector and the caller should see the failure rather than wait.
DELETE_TIMEOUT_SECONDS = 30


@dataclass
class DeleteResult:
    ok: bool
    step: str          # "" on success, otherwise one of: connect|auth|config|delete
    message: str       # "" on success


def _build_delete_argv(source, entry_path: str) -> tuple[list[str], str, str]:
    """Mirror of entry_content._build_fetch_argv but for the `delete`
    subcommand. Returns (argv, password, key_passphrase). Raises
    ValueError on missing binary or missing per-type required fields."""
    binary = scanner_binary_path()
    if not binary:
        raise ValueError("akashic-scanner binary not found on PATH")

    cfg = source.connection_config or {}
    argv = [
        binary, "delete",
        "--type", source.type,
        "--path", entry_path,
        "--password-stdin",
    ]
    if source.type == "ssh":
        kh = (cfg.get("known_hosts_path") or "").strip()
        if not kh:
            raise ValueError("ssh source missing known_hosts_path; refusing to delete")
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
    # local and nfs need no extra connection args — the path itself
    # carries the location.

    password = cfg.get("password") or cfg.get("secret_access_key") or ""
    key_passphrase = cfg.get("key_passphrase") or ""
    return argv, password, key_passphrase


async def delete_copy(source, entry_path: str) -> DeleteResult:
    """Run `akashic-scanner delete` for one file and return the outcome.
    Never raises for a connector-level failure — the api wants to surface
    those per-row, not 500 the whole request.
    """
    # Light path validation. For local/nfs we already know the source
    # root and can lexically refuse traversal. For remote we just block
    # NUL bytes and explicit `..` segments — the remote server is the
    # real authority on what's reachable.
    cfg = source.connection_config or {}
    try:
        if source.type == "local":
            root = str(cfg.get("path") or "")
            if root:
                validate_local_path(root, entry_path)
        elif source.type == "nfs":
            # NFS source mounts at config.export_path; api container
            # sees that path locally. Use the same lexical check.
            root = str(cfg.get("export_path") or "")
            if root:
                validate_local_path(root, entry_path)
        else:
            validate_remote_path(entry_path)
    except PathTraversal as exc:
        return DeleteResult(ok=False, step="config", message=str(exc))

    try:
        argv, password, key_passphrase = _build_delete_argv(source, entry_path)
    except ValueError as exc:
        return DeleteResult(ok=False, step="config", message=str(exc))

    payload = stdin_creds_payload(
        password=password, key_passphrase=key_passphrase,
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Inherit minimal env. The scanner doesn't need
            # AKASHIC_API_URL/KEY for a delete (no api callbacks).
            env={"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")},
        )
    except Exception as exc:  # noqa: BLE001
        return DeleteResult(ok=False, step="config", message=f"spawn failed: {exc}")

    try:
        if proc.stdin is not None:
            proc.stdin.write(payload.encode())
            await proc.stdin.drain()
            proc.stdin.close()
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=DELETE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return DeleteResult(
                ok=False,
                step="delete",
                message=f"timed out after {DELETE_TIMEOUT_SECONDS}s",
            )
    except Exception as exc:  # noqa: BLE001
        return DeleteResult(ok=False, step="delete", message=f"i/o error: {exc}")

    rc = proc.returncode or 0
    if rc == 0:
        return DeleteResult(ok=True, step="", message="")

    err = (stderr or b"").decode(errors="replace").strip()
    step, _, msg = err.partition(":")
    return DeleteResult(
        ok=False,
        step=step.strip() or "delete",
        message=msg.strip() or f"scanner exited {rc}",
    )
