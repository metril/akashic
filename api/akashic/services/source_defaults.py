"""Server-side defaults for fields the user can leave unset on
source create. Currently only `is_removable`, but the module is the
natural home for any future "infer from type/path" logic.
"""
from __future__ import annotations


# Local-mount prefixes that almost always indicate removable / external
# storage. /media + /run/media are the systemd-udev USB convention;
# /mnt is the conventional manual-mount tree; /Volumes is macOS.
# Anything else (/, /home, /var, /srv, …) is treated as fixed by
# default — the user can still flip the checkbox manually.
_REMOVABLE_LOCAL_PREFIXES = ("/media/", "/run/media/", "/mnt/", "/Volumes/")


def infer_is_removable(source_type: str, connection_config: dict | None) -> bool:
    """Best-effort default for the `is_removable` flag when the user
    didn't set one explicitly on create. Erring on the side of false:
    a false-positive ("flagged removable but actually fixed") just
    means an extra Check-now badge in the UI; a false-negative
    ("flagged fixed but actually removable") means the user has to
    flip the checkbox in the edit form."""
    cfg = connection_config or {}
    if source_type == "local":
        path = (cfg.get("path") or "").strip()
        return any(path.startswith(prefix) for prefix in _REMOVABLE_LOCAL_PREFIXES)
    # Network sources (smb/nfs/s3) default to fixed — the user
    # opts in via the form for genuinely-intermittent remotes.
    return False
