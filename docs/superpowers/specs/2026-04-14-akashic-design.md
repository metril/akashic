# Akashic: Universal File Index

## Overview

Akashic is a tool for indexing files and folders across multiple servers and storage media. It provides searchable metadata and full-text content search even when sources are offline, so users always know where to find their data. Scanners run periodically to keep indexes current.

## Architecture

**Hybrid Go + Python stack** chosen to combine Go's fast filesystem traversal with Python's rich text extraction ecosystem.

### Components

1. **Akashic Scanner** (Go) - Fast filesystem traversal, metadata collection, hashing
2. **Akashic API** (Python/FastAPI) - Core backend, text extraction, search orchestration, auth
3. **Akashic Web** (React/TypeScript) - Browser-based search and management UI
4. **Akashic CLI** (Go) - Command-line client wrapping the REST API, single-binary distribution
5. **Infrastructure** - PostgreSQL (metadata), Meilisearch (full-text search), Redis (job queue)

### System Diagram

```
+---------------+   +---------------+   +----------------+
|  Scanner      |   |  Scanner      |   |  Scanner       |
|  (Go agent)   |   |  (Go agent)   |   |  (Go central)  |
|  on Host A    |   |  on Host B    |   |  local/SSH/    |
+-------+-------+   +-------+-------+   |  SMB/NFS/S3   |
        |                   |            +-------+--------+
        +--------+----------+                    |
                 v                               v
         +--------------------------------------+
         |         Akashic API                  |
         |         (Python / FastAPI)           |
         |  +------------+  +-----------+       |
         |  | Extraction |  | Auth/RBAC |       |
         |  | Pipeline   |  | SSO/LDAP  |       |
         |  +------------+  +-----------+       |
         +---+----------+----------+------------+
             |          |          |
        +----v---+ +----v----+ +--v---------+
        |Postgres| |Meili-   | |Redis       |
        |(meta)  | |search   | |(job queue) |
        +--------+ +---------+ +------------+
             ^          ^
             |          |
    +--------+----+ +---+----------+
    | Web UI      | | CLI          |
    | (React/TS)  | | (Python/Go)  |
    +-------------+ +--------------+
             ^
             |
    +--------+--------+
    | Home Assistant   |
    | Integration      |
    +------------------+
```

## Scanner (Go)

### Operating Modes

- **Central mode**: Runs on the Akashic server, connects outbound to sources via SSH/SFTP, SMB/CIFS, NFS mounts, S3-compatible APIs, or local filesystem paths (USB drives, mounted shares).
- **Agent mode**: Lightweight binary deployed on remote hosts. Scans local filesystems and pushes results to the Akashic API over HTTPS. Useful for firewalled or isolated networks.

### Data Collected Per File

| Field | Description |
|-------|-------------|
| `path` | Full absolute path on the source |
| `filename` | File name with extension |
| `extension` | File extension (lowercased) |
| `size_bytes` | File size |
| `mime_type` | Detected via magic bytes (not just extension) |
| `content_hash` | BLAKE3 hash of file contents |
| `permissions` | File permission bits |
| `owner` / `group` | Ownership info (where available) |
| `created_at` | Filesystem creation time |
| `modified_at` | Filesystem modification time |
| `accessed_at` | Filesystem access time |

### Scanning Behavior

- **Incremental scans**: Compares `mtime` against last scan to identify changed files. Only re-hashes/re-processes changed files.
- **Full rescan**: Re-hashes everything to detect bit-rot or silent modifications.
- **Configurable excludes**: Glob patterns to skip (e.g., `.git`, `node_modules`, `*.tmp`, `Thumbs.db`).
- **Rate limiting**: Configurable bandwidth throttling for remote sources to avoid saturating networks.
- **Batched upload**: Sends results to the API in batches (default 1000 files per batch) with retry logic.
- **Deleted file detection**: Files present in previous scan but absent in current scan are reported as deleted.

### Source Connectors

| Connector | Protocol | Use Case |
|-----------|----------|----------|
| Local | Direct filesystem access | USB drives, locally-mounted shares |
| SSH/SFTP | SSH protocol | Linux/macOS servers |
| SMB/CIFS | SMB protocol | Windows shares, some NAS devices |
| NFS | NFS mount | NAS devices exposing NFS shares |
| S3 | S3 API | AWS S3, MinIO, Backblaze B2, etc. |

Each connector implements a common interface for listing, stating, and reading files. New connectors can be added by implementing this interface.

## API Backend (Python/FastAPI)

### API Domains

#### Sources
- CRUD operations for source configurations
- Fields: name, type, connection config, scan schedule (cron), access permissions
- Connection credentials stored encrypted or via external secrets manager reference
- Status tracking: online, offline, scanning, error

#### Scans
- Trigger on-demand scans (full or incremental)
- View scan history with stats (files found/new/changed/deleted)
- Schedule recurring scans via cron expressions
- Cancel running scans

#### Search
- Full-text search across filenames, paths, and extracted content
- Faceted filters: source, file type/extension, size range, date range, path pattern, tags, MIME type
- Results include: file path, source name, source status (online/offline), size, dates, relevance score
- Saved searches / search history

#### Files
- File detail view with all metadata
- View all locations of a file (by content hash) across sources
- View version history (content changes over time at a given path)
- View movement history (same hash appearing at different paths)
- Browse directory tree for a source

#### Directories
- Directory-level metadata, tags, and notes
- Directory size aggregation
- Directory search and browse

#### Duplicates
- List duplicate groups (files sharing the same content hash across different paths/sources)
- Filter by source, minimum size, file type
- Storage savings report (how much space would be freed by deduplication)

#### Tags
- User-defined tags on files and directories
- Bulk tagging operations
- Tag-based search and filtering

#### Users & Auth
- JWT-based authentication
- SSO/OIDC support (Authentik, Keycloak, Authelia, Google, etc.)
- LDAP bind for enterprise environments
- Local account fallback
- Roles: admin (full access), viewer (read-only), scanner (agent API key)
- Per-source access permissions

#### Webhooks
- Configurable per event type
- Events: `scan.completed`, `scan.failed`, `duplicates.found`, `source.offline`, `source.online`
- HMAC signature verification on delivery
- Retry with exponential backoff

#### Storage Analytics
- Storage breakdown by source, file type, age
- Growth trends over time
- Largest files and directories
- Stale data identification (files not accessed in X months)
- Duplicate storage waste report

#### Data Purge
- Purge scan data for a specific source (remove all indexed files for that source)
- Purge old file versions beyond a retention window
- Purge soft-deleted files older than a threshold
- Purge extracted text / search index entries for specific sources
- Audit log for all purge operations

### Text Extraction Pipeline

Triggered asynchronously after scan ingest via Redis job queue.

| MIME Category | Extractor | Details |
|---------------|-----------|---------|
| Documents (PDF, Word, Excel, PPT) | Apache Tika | Full text extraction |
| Plain text, code, config | Direct read | With encoding detection (chardet) |
| Images (JPEG, TIFF, PNG) | ExifTool | EXIF, IPTC, XMP metadata |
| Audio (MP3, FLAC, etc.) | ExifTool / mutagen | ID3, Vorbis, FLAC tags |
| Video (MP4, MKV, etc.) | ExifTool / ffprobe | Container metadata, codec info |
| Archives (ZIP, TAR, RAR) | Python stdlib / rarfile | List contents without full extraction |

- Extraction is idempotent: only re-runs when content hash changes
- Extracted text is indexed into Meilisearch
- Failed extractions are logged and retried with backoff
- Configurable max file size for content extraction (skip huge binaries)

## Data Model (PostgreSQL)

### Core Tables

```sql
-- Source configurations
sources (
    id              UUID PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    type            TEXT NOT NULL,  -- ssh, smb, nfs, s3, local
    connection_config JSONB NOT NULL,  -- host, port, path, credentials_ref
    scan_schedule   TEXT,  -- cron expression
    exclude_patterns TEXT[],
    last_scan_at    TIMESTAMPTZ,
    status          TEXT DEFAULT 'offline',  -- online, offline, scanning, error
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
)

-- Indexed files
files (
    id              UUID PRIMARY KEY,
    source_id       UUID REFERENCES sources(id),
    path            TEXT NOT NULL,
    filename        TEXT NOT NULL,
    extension       TEXT,
    size_bytes      BIGINT,
    mime_type       TEXT,
    content_hash    TEXT,  -- BLAKE3
    permissions     TEXT,
    owner           TEXT,
    file_group      TEXT,
    fs_created_at   TIMESTAMPTZ,
    fs_modified_at  TIMESTAMPTZ,
    fs_accessed_at  TIMESTAMPTZ,
    first_seen_at   TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ DEFAULT NOW(),
    is_deleted      BOOLEAN DEFAULT FALSE,
    deleted_at      TIMESTAMPTZ,
    UNIQUE(source_id, path)
)

-- File version history (tracks content changes at a given path)
file_versions (
    id              UUID PRIMARY KEY,
    file_id         UUID REFERENCES files(id),
    content_hash    TEXT NOT NULL,
    size_bytes      BIGINT,
    detected_at     TIMESTAMPTZ DEFAULT NOW(),
    scan_id         UUID REFERENCES scans(id)
)

-- File movement tracking
file_events (
    id              UUID PRIMARY KEY,
    event_type      TEXT NOT NULL,  -- moved, copied, deleted, restored
    content_hash    TEXT NOT NULL,
    old_source_id   UUID REFERENCES sources(id),
    old_path        TEXT,
    new_source_id   UUID REFERENCES sources(id),
    new_path        TEXT,
    detected_at     TIMESTAMPTZ DEFAULT NOW(),
    scan_id         UUID REFERENCES scans(id)
)

-- Directories with metadata
directories (
    id              UUID PRIMARY KEY,
    source_id       UUID REFERENCES sources(id),
    path            TEXT NOT NULL,
    name            TEXT NOT NULL,
    file_count      INTEGER DEFAULT 0,
    total_size_bytes BIGINT DEFAULT 0,
    first_seen_at   TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ DEFAULT NOW(),
    is_deleted      BOOLEAN DEFAULT FALSE,
    notes           TEXT,
    UNIQUE(source_id, path)
)

-- Duplicate groups (materialized from content_hash)
duplicate_groups (
    id              UUID PRIMARY KEY,
    content_hash    TEXT NOT NULL UNIQUE,
    file_count      INTEGER NOT NULL,
    total_size_bytes BIGINT NOT NULL,
    wasted_bytes    BIGINT NOT NULL,  -- (file_count - 1) * file_size
    first_detected_at TIMESTAMPTZ DEFAULT NOW()
)

-- Scan records
scans (
    id              UUID PRIMARY KEY,
    source_id       UUID REFERENCES sources(id),
    scan_type       TEXT NOT NULL,  -- incremental, full
    status          TEXT DEFAULT 'pending',  -- pending, running, completed, failed
    files_found     INTEGER DEFAULT 0,
    files_new       INTEGER DEFAULT 0,
    files_changed   INTEGER DEFAULT 0,
    files_deleted   INTEGER DEFAULT 0,
    bytes_scanned   BIGINT DEFAULT 0,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    error_message   TEXT
)

-- Tags
tags (
    id              UUID PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    color           TEXT,
    created_by      UUID REFERENCES users(id)
)

file_tags (
    file_id         UUID REFERENCES files(id),
    tag_id          UUID REFERENCES tags(id),
    tagged_by       UUID REFERENCES users(id),
    tagged_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (file_id, tag_id)
)

directory_tags (
    directory_id    UUID REFERENCES directories(id),
    tag_id          UUID REFERENCES tags(id),
    tagged_by       UUID REFERENCES users(id),
    tagged_at       TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (directory_id, tag_id)
)

-- Users and auth
users (
    id              UUID PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    email           TEXT,
    password_hash   TEXT,  -- NULL for SSO-only users
    role            TEXT DEFAULT 'viewer',  -- admin, viewer, scanner
    auth_provider   TEXT DEFAULT 'local',  -- local, oidc, ldap
    external_id     TEXT,  -- ID from external auth provider
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ
)

source_permissions (
    user_id         UUID REFERENCES users(id),
    source_id       UUID REFERENCES sources(id),
    access_level    TEXT DEFAULT 'read',  -- read, admin
    PRIMARY KEY (user_id, source_id)
)

-- Webhooks
webhooks (
    id              UUID PRIMARY KEY,
    user_id         UUID REFERENCES users(id),
    event_type      TEXT NOT NULL,
    url             TEXT NOT NULL,
    secret          TEXT NOT NULL,
    enabled         BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
)

-- API keys (for scanner agents and CLI)
api_keys (
    id              UUID PRIMARY KEY,
    user_id         UUID REFERENCES users(id),
    name            TEXT NOT NULL,
    key_hash        TEXT NOT NULL,
    permissions     TEXT[] DEFAULT '{}',
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ
)

-- Purge audit log
purge_log (
    id              UUID PRIMARY KEY,
    purge_type      TEXT NOT NULL,  -- source, versions, deleted, extraction
    target          TEXT NOT NULL,  -- source name or description
    records_removed INTEGER,
    performed_by    UUID REFERENCES users(id),
    performed_at    TIMESTAMPTZ DEFAULT NOW()
)
```

### Key Indexes

```sql
CREATE INDEX idx_files_source_id ON files(source_id);
CREATE INDEX idx_files_content_hash ON files(content_hash);
CREATE INDEX idx_files_extension ON files(extension);
CREATE INDEX idx_files_filename ON files(filename);
CREATE INDEX idx_files_path ON files USING gin(path gin_trgm_ops);
CREATE INDEX idx_files_size ON files(size_bytes);
CREATE INDEX idx_files_modified ON files(fs_modified_at);
CREATE INDEX idx_files_last_seen ON files(last_seen_at);
CREATE INDEX idx_files_is_deleted ON files(is_deleted) WHERE is_deleted = TRUE;
CREATE INDEX idx_file_versions_file_id ON file_versions(file_id);
CREATE INDEX idx_file_events_hash ON file_events(content_hash);
CREATE INDEX idx_directories_source ON directories(source_id);
CREATE INDEX idx_directories_path ON directories USING gin(path gin_trgm_ops);
```

### Meilisearch Index

Mirrors a subset of file data for fast full-text search:

```json
{
    "id": "file UUID",
    "source_id": "...",
    "source_name": "...",
    "path": "/data/reports/q4-2025.pdf",
    "filename": "q4-2025.pdf",
    "extension": "pdf",
    "mime_type": "application/pdf",
    "size_bytes": 1048576,
    "content_text": "Extracted full text content...",
    "tags": ["reports", "finance"],
    "fs_modified_at": 1700000000,
    "directory_path": "/data/reports"
}
```

Searchable attributes: `filename`, `path`, `content_text`, `tags`
Filterable attributes: `source_id`, `extension`, `mime_type`, `size_bytes`, `fs_modified_at`, `tags`
Sortable attributes: `size_bytes`, `fs_modified_at`, `filename`

## Web UI (React/TypeScript)

### Pages

| Page | Description |
|------|-------------|
| **Dashboard** | Stats overview: total files, total storage indexed, source status, recent scans, top duplicate groups, storage growth chart |
| **Search** | Central search bar with autocomplete. Faceted filters (source, type, size, date, tags, path). Results show file path, source, source status (online/offline), size, modified date. Click to see full detail. |
| **Browse** | Tree-based directory browser per source. Folder metadata, tags, notes. Inline file listing. |
| **Duplicates** | Grouped duplicate view showing all copies across sources. Sort by wasted space. Bulk actions. |
| **Sources** | Source management: add/edit/remove. Status indicators. Trigger scans. View scan history. Connection test. |
| **Analytics** | Storage breakdown by source, file type, age. Growth trends. Largest files/directories. Stale data report. |
| **Admin** | User management, RBAC, webhook config, API key management, purge operations with confirmation. |

## CLI

Thin client wrapping the REST API:

```
# Search
akashic search "quarterly report" --source nas2 --type pdf --after 2024-01
akashic search --tag finance --min-size 1MB

# Sources
akashic sources list
akashic sources add --name "backup-server" --type ssh --host 10.0.0.5 --path /data
akashic sources status
akashic sources test --name backup-server

# Scans
akashic scan trigger --source backup-server [--full]
akashic scan status
akashic scan history --source backup-server

# Duplicates
akashic duplicates list --min-size 100MB
akashic duplicates report --format json

# Tags
akashic tag add --file /path/to/file --tag important
akashic tag list

# Admin
akashic purge --source old-drive --confirm
akashic analytics storage --by-type
```

## Home Assistant Integration

Custom integration providing:

### Sensors
- `sensor.akashic_total_files` - Total files indexed
- `sensor.akashic_total_storage` - Total storage indexed
- `sensor.akashic_source_<name>_status` - Per-source status (online/offline/scanning)
- `sensor.akashic_source_<name>_file_count` - Per-source file count
- `sensor.akashic_source_<name>_last_scan` - Per-source last scan time

### Binary Sensors
- `binary_sensor.akashic_source_<name>_available` - Is source reachable

### Services
- `akashic.trigger_scan` - Trigger a scan for a source
- `akashic.trigger_full_scan` - Trigger a full rescan

### Automation Examples
- Detect USB drive plugged in -> trigger scan
- Source goes offline -> send notification
- Scan completed with new duplicates -> alert
- Nightly scan schedule managed through HA automations

### Implementation
- Communicates with Akashic API using API key authentication
- Polling interval configurable (default 60s for status, 5min for stats)
- Configuration via config flow (API URL + API key)

## Deployment

Docker Compose as the primary deployment method:

```yaml
services:
  api:
    build: ./api
    ports: ["8000:8000"]
    depends_on: [postgres, meilisearch, redis]

  scanner:
    build: ./scanner
    # Central mode scanner

  web:
    build: ./web
    ports: ["3000:3000"]

  postgres:
    image: postgres:16
    volumes: ["pgdata:/var/lib/postgresql/data"]

  meilisearch:
    image: getmeili/meilisearch:latest
    volumes: ["msdata:/meili_data"]

  redis:
    image: redis:7-alpine

  tika:
    image: apache/tika:latest
    ports: ["9998:9998"]
```

Also supports:
- Single-binary CLI for agent mode (Go scanner binary)
- Helm chart for Kubernetes deployment (future)
- Bare metal installation guide

## Security Considerations

- Source credentials encrypted at rest (Fernet or similar)
- API communication over HTTPS
- Agent-to-API auth via API keys with scoped permissions
- RBAC enforced at API layer - users only see sources they have access to
- Search results filtered by user permissions
- Webhook secrets for HMAC verification
- Purge operations require admin role and confirmation
- Audit logging for sensitive operations

## Verification Plan

### Unit Tests
- Scanner: test each connector (mock filesystem), test incremental scan logic, test hash computation
- API: test each endpoint, test extraction pipeline, test auth/RBAC, test search query building
- CLI: test command parsing and API client

### Integration Tests
- Scanner -> API: test batch ingest flow
- API -> Meilisearch: test search indexing and querying
- API -> PostgreSQL: test data model operations
- Full scan-to-search flow: scan a test directory, verify files appear in search results

### End-to-End Tests
- Deploy via Docker Compose
- Add a test source (local directory)
- Trigger a scan
- Verify files appear in Web UI search
- Verify duplicate detection
- Verify text extraction for sample documents
- Test RBAC (user without access to a source cannot see its files)
- Test purge operation

### Manual Verification
- Web UI: test search, browse, duplicates, analytics pages
- CLI: run all commands against a live instance
- HA integration: add to test HA instance, verify sensors and services
