import uuid
from datetime import datetime

from pydantic import BaseModel


class SourceCreate(BaseModel):
    name: str
    type: str
    connection_config: dict
    scan_schedule: str | None = None
    exclude_patterns: list[str] | None = None
    # Phase 2 multi-scanner — restrict scans to a specific pool. NULL
    # = any registered scanner can claim. Set to a pool tag (e.g.
    # "site-amsterdam") to lock the source to that pool.
    preferred_pool: str | None = None


class SourceUpdate(BaseModel):
    name: str | None = None
    connection_config: dict | None = None
    scan_schedule: str | None = None
    exclude_patterns: list[str] | None = None
    preferred_pool: str | None = None


_SECRET_KEYS = {"password", "secret", "key", "token", "credentials", "private_key"}


def _scrub_config(config: dict) -> dict:
    """Remove sensitive values from connection_config for API responses."""
    return {
        k: "***" if any(s in k.lower() for s in _SECRET_KEYS) else v
        for k, v in config.items()
    }


class SourceResponse(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    connection_config: dict
    scan_schedule: str | None
    exclude_patterns: list[str] | None
    preferred_pool: str | None = None
    last_scan_at: datetime | None
    status: str
    created_at: datetime
    updated_at: datetime
    security_metadata: dict | None = None

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
    scan_schedule: str | None
    preferred_pool: str | None = None
    last_scan_at: datetime | None
    status: str
    summary: str
    created_at: datetime
    updated_at: datetime

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
            scan_schedule=source.scan_schedule,
            preferred_pool=source.preferred_pool,
            last_scan_at=source.last_scan_at,
            status=source.status,
            summary=_summary_for(source),
            created_at=source.created_at,
            updated_at=source.updated_at,
        )


def _summary_for(source) -> str:
    """Compact, type-aware one-liner for SourceCard. Server-side
    mirror of web/src/lib/sources.ts:formatSourceSummary so the
    list payload doesn't need to ship connection_config just to
    render a subtitle.
    """
    cfg: dict = source.connection_config or {}
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
    if t == "ssh":
        user, host = g("username"), g("host")
        port_raw = cfg.get("port")
        port = f":{port_raw}" if isinstance(port_raw, int) and port_raw != 22 else ""
        if user and host:
            return f"{user}@{host}{port}"
        return host or name
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
    return name
