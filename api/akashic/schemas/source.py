import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SourceCreate(BaseModel):
    name: str = Field(description="Display name; must be unique.", examples=['nas-projects'])
    type: str = Field(description="Source type, e.g. `local`, `smb`, `nfs`, `s3`, `webdav`, `paperless`, `immich`, `gdrive`, `onedrive` or `dropbox`.")
    connection_config: dict = Field(description="Connection settings for the type (path, share, bucket, URL, ...). With a host, only share-level fields are needed. Secret-looking keys are masked on read.")
    # Optional FK to a Host row that owns the connection-level config
    # (hostname/credentials). When provided, `connection_config` only
    # needs the share-level fields (path/share/export_path/bucket);
    # the host's config is merged in at scan/test time. NULL is valid
    # only for `local` sources.
    host_id: uuid.UUID | None = Field(default=None, description="Host that supplies hostname and credentials; must have the same type. Not allowed for hostless types such as `local`.")
    scan_schedule: str | None = Field(default=None, description="Cron expression for scheduled scans; null for manual scans only.", examples=['0 2 * * *'])
    exclude_patterns: list[str] | None = Field(default=None, description="Glob patterns to skip while scanning.")
    # Phase 2 multi-scanner — restrict scans to a specific pool. NULL
    # = any registered scanner can claim. Set to a pool tag (e.g.
    # "site-amsterdam") to lock the source to that pool.
    preferred_pool: str | None = Field(default=None, description="Only scanners in this pool may scan the source; null for any pool.")
    # Multi-scanner cap: max distinct scanners that can hold unit leases
    # on a single scan simultaneously. NULL = inherit from the parent
    # host (or the built-in default 1). Bump to N to let N scanners
    # cooperate via the scan_work_units lease primitive.
    max_parallel_scanners: int | None = Field(default=None, description="Scanners that may work on one scan at once; null inherits from the host (default 1).")
    # v0.35.0 — work-unit entry budget. NULL = inherit from the host
    # (or the built-in default 2000). See services/source_config.
    scan_chunk_size: int | None = Field(default=None, description="Entries per work unit; null inherits from the host (default 2000).")
    # External / removable storage hint. None on create → server
    # infers from type/path (USB / network mounts → true; otherwise
    # false). See services/source_defaults.py.
    is_removable: bool | None = Field(default=None, description="Whether the storage may be disconnected (USB or network mounts). Inferred from type and path when omitted.")
    # Optional reusable credential profile. Profile values are merged
    # under inline connection_config keys at scan time.
    credential_profile_id: uuid.UUID | None = Field(default=None, description="Credential profile merged under `connection_config`; must have the same type.")

    model_config = {"json_schema_extra": {'examples': [{'name': 'nas-projects', 'type': 'smb', 'connection_config': {'share': 'projects', 'path': '/'}, 'host_id': '9b2c5a40-1d6e-4f0a-8c7b-3e5d2a1f4b60', 'scan_schedule': '0 2 * * *', 'exclude_patterns': ['*.tmp', '~$*']}]}}


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, description="New display name.")
    host_id: uuid.UUID | None = Field(default=None, description="Attach to a host of the same type. Null detaches (only for hostless types).")
    connection_config: dict | None = Field(default=None, description="Keys to merge into the stored config. A secret sent as `***` keeps its stored value.")
    scan_schedule: str | None = Field(default=None, description="New cron expression; null clears the schedule.")
    exclude_patterns: list[str] | None = Field(default=None, description="New exclude patterns, replacing the list.")
    preferred_pool: str | None = Field(default=None, description="New preferred pool; null for any.")
    max_parallel_scanners: int | None = Field(default=None, description="New scanner cap; null inherits.")
    scan_chunk_size: int | None = Field(default=None, description="New work unit size; null inherits.")
    is_removable: bool | None = Field(default=None, description="New removable-storage hint.")
    credential_profile_id: uuid.UUID | None = Field(default=None, description="Credential profile to merge under the config; same type as the source.")

    model_config = {"json_schema_extra": {'examples': [{'scan_schedule': '0 3 * * 0', 'exclude_patterns': ['*.tmp'], 'max_parallel_scanners': 2}]}}


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
    GET /api/v1/hosts/{id}. Inlining the credentials on every source
    response would be both heavy and a needless surface area.

    `credential_profile_id` is included (v0.31.4) so the source detail
    panel can show that a host-backed source inherits its credentials
    from the host's profile — the source's own `credential_profile_id`
    stays NULL by design. It is an id, not a secret, so it is safe to
    inline.
    """

    id: uuid.UUID = Field(description="Host id.")
    name: str = Field(description="Host name.")
    type: str = Field(description="Host type: `smb`, `nfs` or `s3`.")
    credential_profile_id: uuid.UUID | None = Field(default=None, description="Credential profile the host uses, if any.")

    model_config = {"from_attributes": True}


class SourceResponse(BaseModel):
    id: uuid.UUID = Field(description="Source id.")
    name: str = Field(description="Display name.")
    type: str = Field(description="Source type.")
    host_id: uuid.UUID | None = Field(default=None, description="Host the source belongs to.")
    host: _HostInline | None = Field(default=None, description="Summary of that host.")
    connection_config: dict = Field(description="Connection settings with secret-looking values replaced by `***`.")
    scan_schedule: str | None = Field(description="Cron expression for scheduled scans; null if manual only.")
    exclude_patterns: list[str] | None = Field(description="Glob patterns skipped while scanning.")
    preferred_pool: str | None = Field(default=None, description="Pool that must scan the source; null for any.")
    # NULL = inherit from the host (resolved at scan time). See
    # services/source_config.effective_max_parallel_scanners /
    # effective_chunk_size.
    max_parallel_scanners: int | None = Field(default=None, description="Scanner cap set on the source; null inherits from the host.")
    scan_chunk_size: int | None = Field(default=None, description="Work unit size set on the source; null inherits from the host.")
    last_scan_at: datetime | None = Field(description="When the last scan completed.")
    status: str = Field(description="`online`, `scanning`, `failed` or `offline`.")
    created_at: datetime = Field(description="When the source was created.")
    updated_at: datetime = Field(description="When the source was last changed.")
    security_metadata: dict | None = Field(default=None, description="Source-level security data reported by the scanner (for example an S3 bucket policy).")
    is_removable: bool = Field(default=False, description="Whether the storage may be disconnected.")
    credential_profile_id: uuid.UUID | None = Field(default=None, description="Credential profile merged under the config.")

    model_config = {"from_attributes": True, "json_schema_extra": {'examples': [{'id': '3fa85f64-5717-4562-b3fc-2c963f66afa6', 'name': 'nas-projects', 'type': 'smb', 'connection_config': {'share': 'projects', 'password': '***'}, 'scan_schedule': '0 2 * * *', 'exclude_patterns': ['*.tmp'], 'last_scan_at': '2026-04-27T22:00:00Z', 'status': 'online', 'created_at': '2026-01-10T09:00:00Z', 'updated_at': '2026-04-27T22:00:00Z'}]}}

    def model_post_init(self, __context) -> None:
        self.connection_config = _scrub_config(self.connection_config)


class SourceListResponse(BaseModel):
    """Lean shape for `GET /api/v1/sources`. Drops the heavy fields
    (`connection_config`, `security_metadata`, `exclude_patterns`)
    that the Sources list view never reads — but adds a derived
    `summary` string so the per-card "user@host" / "bucket
    (region)" subtitle still renders without the full config.

    For typical 50-source installs this cuts the list payload
    ~25-30%; bigger wins on installs with rich connection_configs
    (e.g. SMB/SFTP with credentials + known-host blobs).

    Detail panel still uses the full `SourceResponse` via
    `GET /api/v1/sources/{id}` for edit / display purposes.
    """

    id: uuid.UUID = Field(description="Source id.")
    name: str = Field(description="Display name.")
    type: str = Field(description="Source type.")
    host_id: uuid.UUID | None = Field(default=None, description="Host the source belongs to.")
    host: _HostInline | None = Field(default=None, description="Summary of that host.")
    scan_schedule: str | None = Field(description="Cron expression for scheduled scans; null if manual only.")
    preferred_pool: str | None = Field(default=None, description="Pool that must scan the source; null for any.")
    max_parallel_scanners: int | None = Field(default=None, description="Scanner cap set on the source; null inherits.")
    last_scan_at: datetime | None = Field(description="When the last scan completed.")
    status: str = Field(description="`online`, `scanning`, `failed` or `offline`.")
    summary: str = Field(description="Server-rendered one-line description such as `host:/export` or `bucket (region)`.")
    created_at: datetime = Field(description="When the source was created.")
    updated_at: datetime = Field(description="When the source was last changed.")
    is_removable: bool = Field(default=False, description="Whether the storage may be disconnected.")

    model_config = {"from_attributes": True, "json_schema_extra": {'examples': [{'id': '3fa85f64-5717-4562-b3fc-2c963f66afa6', 'name': 'nas-projects', 'type': 'smb', 'scan_schedule': '0 2 * * *', 'last_scan_at': '2026-04-27T22:00:00Z', 'status': 'online', 'summary': '\\\\nas.example.com\\projects', 'created_at': '2026-01-10T09:00:00Z', 'updated_at': '2026-04-27T22:00:00Z'}]}}

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
