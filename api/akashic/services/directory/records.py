"""Backend-neutral records returned by the directory readers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class DirRecord:
    backend: Literal["ad", "authentik", "akashic"]
    ref: str
    kind: Literal["person", "group"]
    display_name: str | None
    username: str | None
    email: str | None
    sid: str | None
    dn: str | None
    group_sids: tuple[str, ...] = ()
    groups_status: Literal["ok", "failed", "none"] = "none"
    uid_number: int | None = None
    gid_number: int | None = None


class DirectoryError(Exception):
    """A directory read failed. The message is already redacted (no secrets)."""


class NotConfigured(Exception):
    """The directory backend has no (complete) configuration."""
