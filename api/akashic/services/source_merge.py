"""Helpers for safely PATCH-ing a Source's `connection_config`.

When the UI edits a saved source, it receives the API's scrubbed config
(passwords masked as `"***"`). If the user then re-saves without retyping
the password, the form sends `"***"` back. A naive overwrite would
clobber the real secret.

`merge_connection_config` drops any incoming value that matches the
masked sentinel for keys whose name suggests a secret. Keys not in the
incoming dict pass through unchanged from `existing`, so partial
updates work too (e.g., the UI sending only the changed field).

`field_diff` builds an audit-safe before/after summary. Secret values
are reported as `set` / `cleared` / `changed` rather than literal values.
"""
from __future__ import annotations

from typing import Any

from akashic.schemas.source import _SECRET_KEYS

# The exact sentinel `_scrub_config()` writes for masked secrets in
# response payloads. The merge replaces this string with the existing
# value rather than overwriting.
_MASKED = "***"


def _is_secret_key(name: str) -> bool:
    n = name.lower()
    return any(token in n for token in _SECRET_KEYS)


def merge_connection_config(existing: dict | None, incoming: dict | None) -> dict:
    """Merge `incoming` over `existing`, preserving real secrets when
    `incoming` carries the masked sentinel `"***"` for a secret-named
    key.

    - Keys present in `incoming` overwrite, unless secret + `"***"`.
    - Keys absent from `incoming` stay as they are in `existing`.
    - Returns a new dict; never mutates the inputs.
    """
    base = dict(existing or {})
    for key, value in (incoming or {}).items():
        if value == _MASKED and _is_secret_key(key):
            continue
        base[key] = value
    return base


def reject_sentinel_in_create(config: dict | None) -> str | None:
    """Validate a connection_config that's being CREATED (no existing
    value to merge against). Returns an error message if any field
    holds the masked sentinel `"***"`, or None if clean.

    On create there is no real secret to preserve, so writing `"***"`
    means writing the literal string — almost certainly a UI bug
    (e.g., the form sent back the scrubbed display value verbatim
    instead of the user's input). Rejecting prevents a corrupted-but-
    looks-healthy source.

    Also rejects `"***"` on non-secret keys regardless of create/edit:
    that's never a meaningful value.
    """
    for key, value in (config or {}).items():
        if value == _MASKED:
            if _is_secret_key(key):
                return (
                    f"connection_config.{key} = \"***\" — refusing to write "
                    "the masked sentinel as a literal value. Type the real "
                    "secret, or omit the field on edit to keep the existing one."
                )
            return (
                f"connection_config.{key} = \"***\" — that's the masked-secret "
                "sentinel; not a valid value for a non-secret field."
            )
    return None


def _redact_for_audit(value: Any, key: str) -> Any:
    """For an audit-payload value: pass through non-secret values, swap
    secrets for a state token so the audit log never carries the real
    credential."""
    if not _is_secret_key(key):
        return value
    if value in (None, ""):
        return "<empty>"
    if value == _MASKED:
        return "<masked-input>"
    return "<set>"


def field_diff(before: dict, after: dict) -> dict[str, dict[str, Any]]:
    """Return per-field diff suitable for an audit-event payload.

    Schema: `{field_name: {"before": <safe>, "after": <safe>}}`.
    Secret-named fields show only state transitions (`<set>` / `<empty>`),
    never the literal value.

    Only fields that actually changed are included; identical values
    are skipped so the audit log isn't padded with no-op rows.
    """
    out: dict[str, dict[str, Any]] = {}
    keys = set(before.keys()) | set(after.keys())
    for k in keys:
        b = before.get(k)
        a = after.get(k)
        if b == a:
            continue
        out[k] = {
            "before": _redact_for_audit(b, k),
            "after": _redact_for_audit(a, k),
        }
    return out
