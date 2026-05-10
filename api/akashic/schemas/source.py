import uuid
from datetime import datetime

from pydantic import BaseModel


class SourceCreate(BaseModel):
    name: str
    type: str
    connection_config: dict
    # Optional FK to a Host row that owns the connection-level config
    # (hostname/credentials). When provided, `connection_config` only
    # needs the share-level fields (path/share/export_path/bucket);
    # the host's config is merged in at scan/test time. NULL is valid
    # only for `local` sources.
    host_id: uuid.UUID | None = None
    scan_schedule: str | None = None
    exclude_patterns: list[str] | None = None
    # Phase 2 multi-scanner — restrict scans to a specific pool. NULL
    # = any registered scanner can claim. Set to a pool tag (e.g.
    # "site-amsterdam") to lock the source to that pool.
    preferred_pool: str | None = None
    # Multi-scanner cap: max number of distinct scanners that can hold
    # unit leases on a single scan simultaneously. Default 1 = legacy
    # one-scanner-per-scan. Bump to N to let N scanners cooperate via
    # the scan_work_units lease primitive.
    max_parallel_scanners: int | None = None
    # External / removable storage hint. None on create → server
    # infers from type/path (USB / network mounts → true; otherwise
    # false). See services/source_defaults.py.
    is_removable: bool | None = None
    # Optional reusable credential profile. Profile values are merged
    # under inline connection_config keys at scan time.
    credential_profile_id: uuid.UUID | None = None


class SourceUpdate(BaseModel):
    name: str | None = None
    host_id: uuid.UUID | None = None
    connection_config: dict | None = None
    scan_schedule: str | None = None
    exclude_patterns: list[str] | None = None
    preferred_pool: str | None = None
    max_parallel_scanners: int | None = None
    is_removable: bool | None = None
    credential_profile_id: uuid.UUID | None = None


_SECRET_KEYS = {"password", "secret", "key", "token", "credentials", "private_key"}


def _scrub_config(config: dict) -> dict:
    """Remove sensitive values from connection_config for API responses."""
    return {
        k: "***" if any(s in k.lower() for s in _SECRET_KEYS) else v
        for k, v in config.items()
    }


class _HostInline(BaseModel):
    """Inline shape carried on a SourceResponse — name + type only.

    Full host details (including masked credentials) live behind
    GET /api/hosts/{id}. Inlining the credentials on every source
    response would be both heavy and a needless surface area.
    """

    id: uuid.UUID
    name: str
    type: str

    model_config = {"from_attributes": True}


class SourceResponse(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    host_id: uuid.UUID | None = None
    host: _HostInline | None = None
    connection_config: dict
    scan_schedule: str | None
    exclude_patterns: list[str] | None
    preferred_pool: str | None = None
    max_parallel_scanners: int = 1
    last_scan_at: datetime | None
    status: str
    created_at: datetime
    updated_at: datetime
    security_metadata: dict | None = None
    is_removable: bool = False
    credential_profile_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}

    def model_post_init(self, __context) -> None:
        self.connection_config = _scrub_config(self.connection_config)


class SourceListResponse(BaseModel):
    """Lean shape for `GET /api/sources`. Drops the heavy fields
    (`connection_config`, `security_metadata`, `exclude_patterns`)
    that the Sources list view never reads — but adds a derived
    `summary` string so the per-card "user@host" / "bucket
    (region)" subtitle still renders without the full config.

    For typical 50-source installs this cuts the list payload
    ~25-30%; bigger wins on installs with rich connection_configs
    (e.g. SMB/SFTP with credentials + known-host blobs).

    Detail panel still uses the full `SourceResponse` via
    `GET /api/sources/{id}` for edit / display purposes.
    """

    id: uuid.UUID
    name: str
    type: str
    host_id: uuid.UUID | None = None
    host: _HostInline | None = None
    scan_schedule: str | None
    preferred_pool: str | None = None
    max_parallel_scanners: int = 1
    last_scan_at: datetime | None
    status: str
    summary: str
    created_at: datetime
    updated_at: datetime
    is_removable: bool = False

    model_config = {"from_attributes": True}

    @classmethod
    def from_source(cls, source) -> "SourceListResponse":
        """Build the lean response from a Source model row.
        Computes the summary string server-side so the client
        doesn't need to ship a full connection_config to render
        the card."""
        return cls(
            id=source.id,
            name=source.name,
            type=source.type,
            host_id=source.host_id,
            host=(
                _HostInline.model_validate(source.host)
                if source.host_id and getattr(source, "host", None) is not None
                else None
            ),
            scan_schedule=source.scan_schedule,
            preferred_pool=source.preferred_pool,
            max_parallel_scanners=source.max_parallel_scanners,
            last_scan_at=source.last_scan_at,
            status=source.status,
            summary=_summary_for(source),
            created_at=source.created_at,
            updated_at=source.updated_at,
            is_removable=source.is_removable,
        )


def _summary_for(source) -> str:
    """Compact, type-aware one-liner for SourceCard. Server-side
    mirror of web/src/lib/sources.ts:formatSourceSummary so the
    list payload doesn't need to ship connection_config just to
    render a subtitle.
    """
    # Merge host config (if any) under the source's share-only fields
    # so the summary still surfaces host:share / user@host even when
    # the host-shaped keys live on the parent Host row.
    host = getattr(source, "host", None)
    cfg: dict = {}
    if host is not None:
        cfg.update(dict(getattr(host, "connection_config", None) or {}))
    cfg.update(dict(source.connection_config or {}))
    name = source.name
    g = lambda k: cfg.get(k) if isinstance(cfg.get(k), str) else ""

    t = source.type
    if t == "local":
        return g("path") or name
    if t == "nfs":
        host, exp = g("host"), g("export_path")
        if host and exp:
            return f"{host}:{exp}"
        return host or exp or name
    if t == "smb":
        host, share = g("host"), g("share")
        if host and share:
            return f"\\\\{host}\\{share}"
        return host or name
    if t == "s3":
        bucket, region, endpoint = g("bucket"), g("region"), g("endpoint")
        if endpoint:
            return f"{endpoint}/{bucket}"
        if bucket and region:
            return f"{bucket} ({region})"
        return bucket or name
    if t == "paperless":
        # Strip the scheme so the card subtitle doesn't burn 8 chars
        # on https:// — the user already knows the source is HTTP, the
        # interesting part is which paperless instance.
        url = g("url")
        if url:
            for prefix in ("https://", "http://"):
                if url.startswith(prefix):
                    url = url[len(prefix):]
                    break
            return url.rstrip("/") or name
        return name
    if t == "immich":
        url = g("url")
        if url:
            for prefix in ("https://", "http://"):
                if url.startswith(prefix):
                    url = url[len(prefix):]
                    break
            return url.rstrip("/") or name
        return name
    if t == "webdav":
        url = g("url")
        if url:
            for prefix in ("https://", "http://"):
                if url.startswith(prefix):
                    url = url[len(prefix):]
                    break
            return url.rstrip("/") or name
        return name
    if t == "gdrive":
        # The OAuth credential's account_email lives on the
        # SourceOAuthCredential row, not on connection_config — the list
        # payload doesn't have it joined in. Fall back to "My Drive" or
        # the configured folder_id when set.
        folder = g("folder_id")
        return f"Drive folder {folder}" if folder else "Google Drive"
    if t == "onedrive":
        item = g("item_id")
        return f"OneDrive item {item}" if item else "OneDrive"
    if t == "dropbox":
        # Dropbox has no convenient identifier for the subtitle; show
        # the scoped path or fall back to the type name.
        path = g("path")
        return f"Dropbox {path}" if path else "Dropbox"
    return name
