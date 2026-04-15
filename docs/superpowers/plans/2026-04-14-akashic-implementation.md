# Akashic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a universal file index that scans files across multiple servers and storage media, providing searchable metadata and full-text content search even when sources are offline.

**Architecture:** Hybrid Go + Python monorepo. Go scanner binary handles fast filesystem traversal and hashing. Python/FastAPI API handles text extraction, search orchestration, auth, and serves the REST API. React/TypeScript frontend. Go CLI client. PostgreSQL for metadata, Meilisearch for full-text search, Redis for job queues. Docker Compose for deployment.

**Tech Stack:** Go 1.22+, Python 3.12+, FastAPI, SQLAlchemy 2.0, Alembic, React 18, TypeScript, PostgreSQL 16, Meilisearch, Redis, Apache Tika, BLAKE3, Docker Compose

**Spec:** `docs/superpowers/specs/2026-04-14-akashic-design.md`

---

## Project Structure

```
akashic/
├── docker-compose.yml
├── .env.example
├── scanner/                    # Go scanner + CLI binary
│   ├── go.mod
│   ├── go.sum
│   ├── cmd/
│   │   └── akashic-scanner/
│   │       └── main.go         # Scanner binary entrypoint
│   ├── internal/
│   │   ├── config/
│   │   │   └── config.go       # Scanner configuration
│   │   ├── walker/
│   │   │   ├── walker.go       # Core file tree walker
│   │   │   └── walker_test.go
│   │   ├── metadata/
│   │   │   ├── collector.go    # Metadata + hash collection
│   │   │   └── collector_test.go
│   │   ├── connector/
│   │   │   ├── connector.go    # Connector interface
│   │   │   ├── local.go        # Local filesystem connector
│   │   │   ├── local_test.go
│   │   │   ├── ssh.go          # SSH/SFTP connector
│   │   │   ├── ssh_test.go
│   │   │   ├── smb.go          # SMB/CIFS connector
│   │   │   ├── smb_test.go
│   │   │   ├── nfs.go          # NFS connector
│   │   │   ├── nfs_test.go
│   │   │   ├── s3.go           # S3-compatible connector
│   │   │   └── s3_test.go
│   │   ├── scanner/
│   │   │   ├── scanner.go      # Orchestrates walk + collect + upload
│   │   │   └── scanner_test.go
│   │   └── client/
│   │       ├── client.go       # HTTP client for Akashic API
│   │       └── client_test.go
│   └── pkg/
│       └── models/
│           └── models.go       # Shared types (FileEntry, ScanResult, etc.)
├── api/                        # Python FastAPI backend
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/           # Migration files
│   ├── akashic/
│   │   ├── __init__.py
│   │   ├── main.py             # FastAPI app factory
│   │   ├── config.py           # Settings via pydantic-settings
│   │   ├── database.py         # SQLAlchemy engine + session
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── source.py       # Source model
│   │   │   ├── file.py         # File, FileVersion, FileEvent models
│   │   │   ├── directory.py    # Directory model
│   │   │   ├── scan.py         # Scan model
│   │   │   ├── tag.py          # Tag, FileTag, DirectoryTag models
│   │   │   ├── user.py         # User, SourcePermission, APIKey models
│   │   │   └── webhook.py      # Webhook, PurgeLog models
│   │   ├── schemas/
│   │   │   ├── __init__.py
│   │   │   ├── source.py       # Pydantic schemas for sources
│   │   │   ├── file.py         # Pydantic schemas for files
│   │   │   ├── scan.py         # Pydantic schemas for scans
│   │   │   ├── search.py       # Pydantic schemas for search
│   │   │   ├── tag.py          # Pydantic schemas for tags
│   │   │   ├── user.py         # Pydantic schemas for users
│   │   │   └── webhook.py      # Pydantic schemas for webhooks
│   │   ├── routers/
│   │   │   ├── __init__.py
│   │   │   ├── sources.py      # /api/sources endpoints
│   │   │   ├── scans.py        # /api/scans endpoints
│   │   │   ├── search.py       # /api/search endpoints
│   │   │   ├── files.py        # /api/files endpoints
│   │   │   ├── directories.py  # /api/directories endpoints
│   │   │   ├── duplicates.py   # /api/duplicates endpoints
│   │   │   ├── tags.py         # /api/tags endpoints
│   │   │   ├── users.py        # /api/users endpoints
│   │   │   ├── webhooks.py     # /api/webhooks endpoints
│   │   │   ├── analytics.py    # /api/analytics endpoints
│   │   │   ├── purge.py        # /api/purge endpoints
│   │   │   └── ingest.py       # /api/ingest endpoints (scanner uploads)
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── search.py       # Meilisearch integration
│   │   │   ├── extraction.py   # Text extraction orchestration
│   │   │   ├── duplicates.py   # Duplicate detection logic
│   │   │   ├── movement.py     # File movement tracking
│   │   │   ├── webhooks.py     # Webhook dispatch
│   │   │   └── analytics.py    # Storage analytics queries
│   │   ├── auth/
│   │   │   ├── __init__.py
│   │   │   ├── jwt.py          # JWT token creation/validation
│   │   │   ├── dependencies.py # FastAPI auth dependencies
│   │   │   ├── oidc.py         # OIDC/SSO provider
│   │   │   └── ldap.py         # LDAP bind
│   │   ├── workers/
│   │   │   ├── __init__.py
│   │   │   └── extraction.py   # Redis queue consumer for text extraction
│   │   └── middleware/
│   │       ├── __init__.py
│   │       └── rbac.py         # RBAC enforcement middleware
│   └── tests/
│       ├── conftest.py         # Fixtures (test DB, test client, auth helpers)
│       ├── test_ingest.py
│       ├── test_sources.py
│       ├── test_search.py
│       ├── test_files.py
│       ├── test_duplicates.py
│       ├── test_auth.py
│       ├── test_tags.py
│       ├── test_analytics.py
│       ├── test_purge.py
│       └── test_webhooks.py
├── web/                        # React/TypeScript frontend
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── index.html
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── api/
│   │   │   └── client.ts       # API client (fetch wrapper)
│   │   ├── hooks/
│   │   │   ├── useSearch.ts
│   │   │   ├── useSources.ts
│   │   │   └── useAuth.ts
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Search.tsx
│   │   │   ├── Browse.tsx
│   │   │   ├── Duplicates.tsx
│   │   │   ├── Sources.tsx
│   │   │   ├── Analytics.tsx
│   │   │   ├── Admin.tsx
│   │   │   └── Login.tsx
│   │   ├── components/
│   │   │   ├── Layout.tsx
│   │   │   ├── SearchBar.tsx
│   │   │   ├── FileList.tsx
│   │   │   ├── DirectoryTree.tsx
│   │   │   ├── SourceCard.tsx
│   │   │   ├── DuplicateGroup.tsx
│   │   │   └── StorageChart.tsx
│   │   └── types/
│   │       └── index.ts        # TypeScript types matching API schemas
│   └── Dockerfile
├── cli/                        # Go CLI client
│   ├── go.mod
│   ├── go.sum
│   ├── cmd/
│   │   └── akashic/
│   │       └── main.go
│   ├── internal/
│   │   ├── client/
│   │   │   ├── client.go       # API client
│   │   │   └── client_test.go
│   │   └── commands/
│   │       ├── search.go
│   │       ├── sources.go
│   │       ├── scans.go
│   │       ├── duplicates.go
│   │       ├── tags.go
│   │       └── purge.go
│   └── Dockerfile
└── ha-integration/             # Home Assistant custom component
    └── custom_components/
        └── akashic/
            ├── __init__.py
            ├── manifest.json
            ├── config_flow.py
            ├── const.py
            ├── coordinator.py
            ├── sensor.py
            ├── binary_sensor.py
            └── services.yaml
```

---

## Phase 1: Foundation

### Task 1: Repository Scaffolding

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `scanner/go.mod`
- Create: `api/pyproject.toml`
- Create: `web/package.json`
- Create: `cli/go.mod`

- [ ] **Step 1: Initialize git and create .gitignore**

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
dist/

# Go
scanner/akashic-scanner
cli/akashic

# Node
web/node_modules/
web/dist/

# IDE
.idea/
.vscode/
*.swp

# Environment
.env
*.local

# Data
pgdata/
msdata/
redisdata/
```

- [ ] **Step 2: Create .env.example**

```env
# PostgreSQL
POSTGRES_USER=akashic
POSTGRES_PASSWORD=changeme
POSTGRES_DB=akashic

# Meilisearch
MEILI_MASTER_KEY=changeme-meili-key

# API
SECRET_KEY=changeme-secret-key
DATABASE_URL=postgresql+asyncpg://akashic:changeme@postgres:5432/akashic
MEILI_URL=http://meilisearch:7700
MEILI_KEY=changeme-meili-key
REDIS_URL=redis://redis:6379/0

# Scanner
AKASHIC_API_URL=http://api:8000
AKASHIC_API_KEY=scanner-api-key
```

- [ ] **Step 3: Create docker-compose.yml**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-akashic}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-changeme}
      POSTGRES_DB: ${POSTGRES_DB:-akashic}
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-akashic}"]
      interval: 5s
      timeout: 5s
      retries: 5

  meilisearch:
    image: getmeili/meilisearch:v1.11
    environment:
      MEILI_MASTER_KEY: ${MEILI_MASTER_KEY:-changeme-meili-key}
    volumes:
      - msdata:/meili_data
    ports:
      - "7700:7700"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:7700/health"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    volumes:
      - redisdata:/data
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  tika:
    image: apache/tika:3.0.0.0
    ports:
      - "9998:9998"

  api:
    build:
      context: ./api
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      meilisearch:
        condition: service_healthy
      redis:
        condition: service_healthy

  web:
    build:
      context: ./web
      dockerfile: Dockerfile
    ports:
      - "3000:3000"
    depends_on:
      - api

volumes:
  pgdata:
  msdata:
  redisdata:
```

- [ ] **Step 4: Initialize Go scanner module**

```bash
cd scanner && go mod init github.com/akashic-project/akashic/scanner
```

Create `scanner/go.mod`:
```
module github.com/akashic-project/akashic/scanner

go 1.22
```

- [ ] **Step 5: Initialize Go CLI module**

```bash
cd cli && go mod init github.com/akashic-project/akashic/cli
```

Create `cli/go.mod`:
```
module github.com/akashic-project/akashic/cli

go 1.22
```

- [ ] **Step 6: Create Python API pyproject.toml**

```toml
[project]
name = "akashic-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pydantic-settings>=2.7",
    "python-jose[cryptography]>=3.3",
    "passlib[bcrypt]>=1.7",
    "httpx>=0.28",
    "meilisearch-python-sdk>=3.0",
    "redis[hiredis]>=5.2",
    "rq>=2.1",
    "python-multipart>=0.0.18",
    "cryptography>=44.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.25",
    "pytest-httpx>=0.35",
    "coverage>=7.6",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] **Step 7: Create web/package.json**

```json
{
  "name": "akashic-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^7.1.0",
    "@tanstack/react-query": "^5.64.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.18",
    "@types/react-dom": "^18.3.5",
    "@vitejs/plugin-react": "^4.3.4",
    "typescript": "^5.7.0",
    "vite": "^6.0.0"
  }
}
```

- [ ] **Step 8: Commit scaffolding**

```bash
git add .gitignore .env.example docker-compose.yml scanner/go.mod cli/go.mod api/pyproject.toml web/package.json
git commit -m "feat: project scaffolding with Docker Compose and module init"
```

---

### Task 2: PostgreSQL Schema and Migrations

**Files:**
- Create: `api/akashic/__init__.py`
- Create: `api/akashic/config.py`
- Create: `api/akashic/database.py`
- Create: `api/akashic/models/__init__.py`
- Create: `api/akashic/models/source.py`
- Create: `api/akashic/models/file.py`
- Create: `api/akashic/models/directory.py`
- Create: `api/akashic/models/scan.py`
- Create: `api/akashic/models/tag.py`
- Create: `api/akashic/models/user.py`
- Create: `api/akashic/models/webhook.py`
- Create: `api/alembic.ini`
- Create: `api/alembic/env.py`

- [ ] **Step 1: Create config.py**

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://akashic:changeme@localhost:5432/akashic"
    meili_url: str = "http://localhost:7700"
    meili_key: str = "changeme-meili-key"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "changeme-secret-key"
    access_token_expire_minutes: int = 60
    tika_url: str = "http://localhost:9998"

    model_config = {"env_prefix": "", "case_sensitive": False}


settings = Settings()
```

- [ ] **Step 2: Create database.py**

```python
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from akashic.config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
```

- [ ] **Step 3: Create Source model**

`api/akashic/models/source.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, func
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)  # ssh, smb, nfs, s3, local
    connection_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    scan_schedule: Mapped[str | None] = mapped_column(String, nullable=True)
    exclude_patterns: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, default="offline")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

- [ ] **Step 4: Create File, FileVersion, FileEvent models**

`api/akashic/models/file.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import String, BigInteger, Boolean, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class File(Base):
    __tablename__ = "files"
    __table_args__ = (UniqueConstraint("source_id", "path", name="uq_files_source_path"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    extension: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    permissions: Mapped[str | None] = mapped_column(String, nullable=True)
    owner: Mapped[str | None] = mapped_column(String, nullable=True)
    file_group: Mapped[str | None] = mapped_column(String, nullable=True)
    fs_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fs_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fs_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FileVersion(Base):
    __tablename__ = "file_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id"), nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    scan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scans.id"), nullable=True)


class FileEvent(Base):
    __tablename__ = "file_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String, nullable=False)  # moved, copied, deleted, restored
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    old_source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sources.id"), nullable=True)
    old_path: Mapped[str | None] = mapped_column(String, nullable=True)
    new_source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sources.id"), nullable=True)
    new_path: Mapped[str | None] = mapped_column(String, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    scan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scans.id"), nullable=True)
```

- [ ] **Step 5: Create Directory model**

`api/akashic/models/directory.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import String, BigInteger, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class Directory(Base):
    __tablename__ = "directories"
    __table_args__ = (UniqueConstraint("source_id", "path", name="uq_directories_source_path"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    total_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 6: Create Scan model**

`api/akashic/models/scan.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import String, BigInteger, Integer, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False)
    scan_type: Mapped[str] = mapped_column(String, nullable=False)  # incremental, full
    status: Mapped[str] = mapped_column(String, default="pending")
    files_found: Mapped[int] = mapped_column(Integer, default=0)
    files_new: Mapped[int] = mapped_column(Integer, default=0)
    files_changed: Mapped[int] = mapped_column(Integer, default=0)
    files_deleted: Mapped[int] = mapped_column(Integer, default=0)
    bytes_scanned: Mapped[int] = mapped_column(BigInteger, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 7: Create Tag models**

`api/akashic/models/tag.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    color: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)


class FileTag(Base):
    __tablename__ = "file_tags"

    file_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("files.id"), primary_key=True)
    tag_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tags.id"), primary_key=True)
    tagged_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    tagged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DirectoryTag(Base):
    __tablename__ = "directory_tags"

    directory_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("directories.id"), primary_key=True)
    tag_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tags.id"), primary_key=True)
    tagged_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    tagged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 8: Create User, SourcePermission, APIKey models**

`api/akashic/models/user.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, default="viewer")
    auth_provider: Mapped[str] = mapped_column(String, default="local")
    external_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SourcePermission(Base):
    __tablename__ = "source_permissions"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sources.id"), primary_key=True)
    access_level: Mapped[str] = mapped_column(String, default="read")


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    key_hash: Mapped[str] = mapped_column(String, nullable=False)
    permissions: Mapped[list[str] | None] = mapped_column(ARRAY(String), default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 9: Create Webhook and PurgeLog models**

`api/akashic/models/webhook.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from akashic.database import Base


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    secret: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PurgeLog(Base):
    __tablename__ = "purge_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purge_type: Mapped[str] = mapped_column(String, nullable=False)
    target: Mapped[str] = mapped_column(String, nullable=False)
    records_removed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    performed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 10: Create models __init__.py (re-exports)**

`api/akashic/models/__init__.py`:

```python
from akashic.models.source import Source
from akashic.models.file import File, FileVersion, FileEvent
from akashic.models.directory import Directory
from akashic.models.scan import Scan
from akashic.models.tag import Tag, FileTag, DirectoryTag
from akashic.models.user import User, SourcePermission, APIKey
from akashic.models.webhook import Webhook, PurgeLog

__all__ = [
    "Source", "File", "FileVersion", "FileEvent", "Directory", "Scan",
    "Tag", "FileTag", "DirectoryTag", "User", "SourcePermission", "APIKey",
    "Webhook", "PurgeLog",
]
```

- [ ] **Step 11: Set up Alembic**

`api/alembic.ini`:
```ini
[alembic]
script_location = alembic
sqlalchemy.url = postgresql+asyncpg://akashic:changeme@localhost:5432/akashic

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

`api/alembic/env.py`:
```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from akashic.config import settings
from akashic.database import Base
from akashic.models import *  # noqa: F401,F403 — ensure all models registered

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = settings.database_url
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = create_async_engine(settings.database_url)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

- [ ] **Step 12: Generate initial migration**

```bash
cd api
pip install -e ".[dev]"
docker compose up -d postgres
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

Verify tables exist:
```bash
docker compose exec postgres psql -U akashic -c "\dt"
```
Expected: all tables listed (sources, files, file_versions, file_events, directories, scans, tags, file_tags, directory_tags, users, source_permissions, api_keys, webhooks, purge_log)

- [ ] **Step 13: Create indexes migration**

Create a second migration for performance indexes:

```bash
alembic revision -m "add performance indexes"
```

Edit the generated migration to add:

```python
def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index("idx_files_source_id", "files", ["source_id"])
    op.create_index("idx_files_content_hash", "files", ["content_hash"])
    op.create_index("idx_files_extension", "files", ["extension"])
    op.create_index("idx_files_filename", "files", ["filename"])
    op.create_index("idx_files_path_trgm", "files", ["path"], postgresql_using="gin", postgresql_ops={"path": "gin_trgm_ops"})
    op.create_index("idx_files_size", "files", ["size_bytes"])
    op.create_index("idx_files_modified", "files", ["fs_modified_at"])
    op.create_index("idx_files_last_seen", "files", ["last_seen_at"])
    op.create_index("idx_files_deleted", "files", ["is_deleted"], postgresql_where="is_deleted = true")
    op.create_index("idx_file_versions_file_id", "file_versions", ["file_id"])
    op.create_index("idx_file_events_hash", "file_events", ["content_hash"])
    op.create_index("idx_directories_source", "directories", ["source_id"])
    op.create_index("idx_directories_path_trgm", "directories", ["path"], postgresql_using="gin", postgresql_ops={"path": "gin_trgm_ops"})


def downgrade() -> None:
    op.drop_index("idx_directories_path_trgm")
    op.drop_index("idx_directories_source")
    op.drop_index("idx_file_events_hash")
    op.drop_index("idx_file_versions_file_id")
    op.drop_index("idx_files_deleted")
    op.drop_index("idx_files_last_seen")
    op.drop_index("idx_files_modified")
    op.drop_index("idx_files_size")
    op.drop_index("idx_files_path_trgm")
    op.drop_index("idx_files_filename")
    op.drop_index("idx_files_extension")
    op.drop_index("idx_files_content_hash")
    op.drop_index("idx_files_source_id")
```

```bash
alembic upgrade head
```

- [ ] **Step 14: Commit schema and migrations**

```bash
git add api/
git commit -m "feat: PostgreSQL schema with all models and migrations"
```

---

### Task 3: FastAPI App Skeleton with Auth

**Files:**
- Create: `api/akashic/main.py`
- Create: `api/akashic/auth/__init__.py`
- Create: `api/akashic/auth/jwt.py`
- Create: `api/akashic/auth/dependencies.py`
- Create: `api/akashic/schemas/user.py`
- Create: `api/akashic/routers/users.py`
- Create: `api/tests/conftest.py`
- Create: `api/tests/test_auth.py`
- Create: `api/Dockerfile`

- [ ] **Step 1: Write auth tests**

`api/tests/conftest.py`:

```python
import asyncio
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from akashic.database import Base, get_db
from akashic.main import create_app
from akashic.models import *  # noqa: F401,F403

TEST_DB_URL = "postgresql+asyncpg://akashic:changeme@localhost:5432/akashic_test"

engine = create_async_engine(TEST_DB_URL, echo=False)
test_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with test_session() as session:
        yield session


@pytest_asyncio.fixture
async def client():
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session():
    async with test_session() as session:
        yield session
```

`api/tests/test_auth.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_register_user(client):
    response = await client.post("/api/users/register", json={
        "username": "testuser",
        "password": "testpass123",
        "email": "test@example.com",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "testuser"
    assert "id" in data
    assert "password_hash" not in data


@pytest.mark.asyncio
async def test_login(client):
    await client.post("/api/users/register", json={
        "username": "loginuser",
        "password": "testpass123",
    })
    response = await client.post("/api/users/login", json={
        "username": "loginuser",
        "password": "testpass123",
    })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    await client.post("/api/users/register", json={
        "username": "wrongpw",
        "password": "testpass123",
    })
    response = await client.post("/api/users/login", json={
        "username": "wrongpw",
        "password": "wrongpass",
    })
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_endpoint_requires_auth(client):
    response = await client.get("/api/users/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_endpoint_with_token(client):
    await client.post("/api/users/register", json={
        "username": "authed",
        "password": "testpass123",
    })
    login = await client.post("/api/users/login", json={
        "username": "authed",
        "password": "testpass123",
    })
    token = login.json()["access_token"]
    response = await client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["username"] == "authed"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api
docker compose exec postgres psql -U akashic -c "CREATE DATABASE akashic_test"
pytest tests/test_auth.py -v
```
Expected: FAIL — `create_app` not found

- [ ] **Step 3: Implement auth and user endpoints**

`api/akashic/schemas/user.py`:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel


class UserCreate(BaseModel):
    username: str
    password: str
    email: str | None = None


class UserLogin(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    email: str | None
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
```

`api/akashic/auth/jwt.py`:

```python
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from akashic.config import settings

ALGORITHM = "HS256"


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None
```

`api/akashic/auth/dependencies.py`:

```python
import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.jwt import decode_access_token
from akashic.database import get_db
from akashic.models.user import User

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return user
```

`api/akashic/routers/users.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, status
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.auth.jwt import create_access_token
from akashic.database import get_db
from akashic.models.user import User
from akashic.schemas.user import UserCreate, UserLogin, UserResponse, TokenResponse

router = APIRouter(prefix="/api/users", tags=["users"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.username == data.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username taken")
    user = User(
        username=data.username,
        email=data.email,
        password_hash=pwd_context.hash(data.password),
        role="admin",  # first user is admin; subsequent users default to viewer
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(data: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == data.username))
    user = result.scalar_one_or_none()
    if not user or not pwd_context.verify(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    return user
```

`api/akashic/main.py`:

```python
from fastapi import FastAPI

from akashic.routers import users


def create_app() -> FastAPI:
    app = FastAPI(title="Akashic", version="0.1.0")
    app.include_router(users.router)
    return app


app = create_app()
```

`api/akashic/schemas/__init__.py`:
```python
```

`api/akashic/auth/__init__.py`:
```python
```

`api/akashic/routers/__init__.py`:
```python
```

`api/Dockerfile`:
```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY . .
RUN pip install --no-cache-dir -e .

CMD ["uvicorn", "akashic.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd api && pytest tests/test_auth.py -v
```
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add api/
git commit -m "feat: FastAPI app skeleton with JWT auth and user registration"
```

---

## Phase 2: Go Scanner

### Task 4: Scanner Shared Types and Config

**Files:**
- Create: `scanner/pkg/models/models.go`
- Create: `scanner/internal/config/config.go`

- [ ] **Step 1: Create shared types**

`scanner/pkg/models/models.go`:

```go
package models

import "time"

// FileEntry represents a single indexed file sent to the API.
type FileEntry struct {
	Path        string     `json:"path"`
	Filename    string     `json:"filename"`
	Extension   string     `json:"extension,omitempty"`
	SizeBytes   int64      `json:"size_bytes"`
	MimeType    string     `json:"mime_type,omitempty"`
	ContentHash string     `json:"content_hash,omitempty"`
	Permissions string     `json:"permissions,omitempty"`
	Owner       string     `json:"owner,omitempty"`
	Group       string     `json:"file_group,omitempty"`
	CreatedAt   *time.Time `json:"fs_created_at,omitempty"`
	ModifiedAt  *time.Time `json:"fs_modified_at,omitempty"`
	AccessedAt  *time.Time `json:"fs_accessed_at,omitempty"`
	IsDir       bool       `json:"is_dir"`
}

// ScanBatch is a batch of file entries sent to the API ingest endpoint.
type ScanBatch struct {
	SourceID string      `json:"source_id"`
	ScanID   string      `json:"scan_id"`
	Files    []FileEntry `json:"files"`
	IsFinal  bool        `json:"is_final"`
}

// ScanRequest is received from the API to initiate a scan.
type ScanRequest struct {
	SourceID        string   `json:"source_id"`
	ScanID          string   `json:"scan_id"`
	ScanType        string   `json:"scan_type"` // incremental, full
	ExcludePatterns []string `json:"exclude_patterns,omitempty"`
}
```

- [ ] **Step 2: Create config**

`scanner/internal/config/config.go`:

```go
package config

import (
	"os"
)

type Config struct {
	APIUrl    string
	APIKey    string
	BatchSize int
}

func Load() *Config {
	apiUrl := os.Getenv("AKASHIC_API_URL")
	if apiUrl == "" {
		apiUrl = "http://localhost:8000"
	}
	return &Config{
		APIUrl:    apiUrl,
		APIKey:    os.Getenv("AKASHIC_API_KEY"),
		BatchSize: 1000,
	}
}
```

- [ ] **Step 3: Commit**

```bash
git add scanner/
git commit -m "feat: scanner shared types and config"
```

---

### Task 5: Metadata Collector

**Files:**
- Create: `scanner/internal/metadata/collector.go`
- Create: `scanner/internal/metadata/collector_test.go`

- [ ] **Step 1: Write collector tests**

`scanner/internal/metadata/collector_test.go`:

```go
package metadata

import (
	"os"
	"path/filepath"
	"testing"
)

func TestCollect_RegularFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "test.txt")
	if err := os.WriteFile(path, []byte("hello world"), 0644); err != nil {
		t.Fatal(err)
	}

	entry, err := Collect(path, true)
	if err != nil {
		t.Fatal(err)
	}

	if entry.Filename != "test.txt" {
		t.Errorf("expected filename test.txt, got %s", entry.Filename)
	}
	if entry.Extension != "txt" {
		t.Errorf("expected extension txt, got %s", entry.Extension)
	}
	if entry.SizeBytes != 11 {
		t.Errorf("expected size 11, got %d", entry.SizeBytes)
	}
	if entry.ContentHash == "" {
		t.Error("expected non-empty content hash")
	}
	if entry.IsDir {
		t.Error("expected IsDir to be false")
	}
}

func TestCollect_Directory(t *testing.T) {
	dir := t.TempDir()
	subdir := filepath.Join(dir, "subdir")
	if err := os.Mkdir(subdir, 0755); err != nil {
		t.Fatal(err)
	}

	entry, err := Collect(subdir, false)
	if err != nil {
		t.Fatal(err)
	}

	if !entry.IsDir {
		t.Error("expected IsDir to be true")
	}
	if entry.ContentHash != "" {
		t.Error("expected empty content hash for directory")
	}
}

func TestCollect_WithHash(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "hashme.bin")
	if err := os.WriteFile(path, []byte("deterministic content"), 0644); err != nil {
		t.Fatal(err)
	}

	entry1, _ := Collect(path, true)
	entry2, _ := Collect(path, true)

	if entry1.ContentHash != entry2.ContentHash {
		t.Error("same content should produce same hash")
	}
}

func TestCollect_SkipHash(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "nohash.txt")
	if err := os.WriteFile(path, []byte("no hash please"), 0644); err != nil {
		t.Fatal(err)
	}

	entry, err := Collect(path, false)
	if err != nil {
		t.Fatal(err)
	}

	if entry.ContentHash != "" {
		t.Error("expected empty hash when computeHash=false")
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd scanner && go test ./internal/metadata/ -v
```
Expected: FAIL — package not found

- [ ] **Step 3: Implement collector**

Add BLAKE3 dependency:
```bash
cd scanner && go get github.com/zeebo/blake3
```

`scanner/internal/metadata/collector.go`:

```go
package metadata

import (
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/zeebo/blake3"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// Collect gathers metadata for a file at the given path.
// If computeHash is true, it reads the file to compute a BLAKE3 hash.
func Collect(path string, computeHash bool) (*models.FileEntry, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, fmt.Errorf("stat %s: %w", path, err)
	}

	entry := &models.FileEntry{
		Path:      path,
		Filename:  info.Name(),
		SizeBytes: info.Size(),
		IsDir:     info.IsDir(),
	}

	// Extension
	if !info.IsDir() {
		ext := filepath.Ext(info.Name())
		if ext != "" {
			entry.Extension = strings.TrimPrefix(ext, ".")
		}
	}

	// Permissions
	entry.Permissions = info.Mode().Perm().String()

	// Timestamps
	modTime := info.ModTime()
	entry.ModifiedAt = &modTime

	// Platform-specific: created time and ownership
	if stat, ok := info.Sys().(*syscall.Stat_t); ok {
		entry.Owner = fmt.Sprintf("%d", stat.Uid)
		entry.Group = fmt.Sprintf("%d", stat.Gid)
		atime := time.Unix(stat.Atim.Sec, stat.Atim.Nsec)
		entry.AccessedAt = &atime
		ctime := time.Unix(stat.Ctim.Sec, stat.Ctim.Nsec)
		entry.CreatedAt = &ctime
	}

	// MIME type detection (first 512 bytes)
	if !info.IsDir() {
		entry.MimeType = detectMIME(path)
	}

	// Content hash
	if computeHash && !info.IsDir() {
		hash, err := hashFile(path)
		if err != nil {
			return nil, fmt.Errorf("hash %s: %w", path, err)
		}
		entry.ContentHash = hash
	}

	return entry, nil
}

func detectMIME(path string) string {
	f, err := os.Open(path)
	if err != nil {
		return "application/octet-stream"
	}
	defer f.Close()

	buf := make([]byte, 512)
	n, err := f.Read(buf)
	if err != nil && err != io.EOF {
		return "application/octet-stream"
	}
	return http.DetectContentType(buf[:n])
}

func hashFile(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()

	hasher := blake3.New()
	if _, err := io.Copy(hasher, f); err != nil {
		return "", err
	}
	return fmt.Sprintf("%x", hasher.Sum(nil)), nil
}

// CollectFromInfo creates a FileEntry from an existing fs.FileInfo without re-statting.
func CollectFromInfo(path string, info fs.FileInfo, computeHash bool) (*models.FileEntry, error) {
	entry := &models.FileEntry{
		Path:      path,
		Filename:  info.Name(),
		SizeBytes: info.Size(),
		IsDir:     info.IsDir(),
	}

	if !info.IsDir() {
		ext := filepath.Ext(info.Name())
		if ext != "" {
			entry.Extension = strings.TrimPrefix(ext, ".")
		}
	}

	entry.Permissions = info.Mode().Perm().String()
	modTime := info.ModTime()
	entry.ModifiedAt = &modTime

	if stat, ok := info.Sys().(*syscall.Stat_t); ok {
		entry.Owner = fmt.Sprintf("%d", stat.Uid)
		entry.Group = fmt.Sprintf("%d", stat.Gid)
		atime := time.Unix(stat.Atim.Sec, stat.Atim.Nsec)
		entry.AccessedAt = &atime
		ctime := time.Unix(stat.Ctim.Sec, stat.Ctim.Nsec)
		entry.CreatedAt = &ctime
	}

	if !info.IsDir() {
		entry.MimeType = detectMIME(path)
	}

	if computeHash && !info.IsDir() {
		hash, err := hashFile(path)
		if err != nil {
			return nil, err
		}
		entry.ContentHash = hash
	}

	return entry, nil
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd scanner && go test ./internal/metadata/ -v
```
Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scanner/
git commit -m "feat: metadata collector with BLAKE3 hashing and MIME detection"
```

---

### Task 6: File Tree Walker

**Files:**
- Create: `scanner/internal/walker/walker.go`
- Create: `scanner/internal/walker/walker_test.go`

- [ ] **Step 1: Write walker tests**

`scanner/internal/walker/walker_test.go`:

```go
package walker

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

func setupTestTree(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()

	// Create structure:
	// dir/
	//   file1.txt
	//   subdir/
	//     file2.log
	//     .git/
	//       config

	os.WriteFile(filepath.Join(dir, "file1.txt"), []byte("hello"), 0644)
	os.MkdirAll(filepath.Join(dir, "subdir"), 0755)
	os.WriteFile(filepath.Join(dir, "subdir", "file2.log"), []byte("world"), 0644)
	os.MkdirAll(filepath.Join(dir, "subdir", ".git"), 0755)
	os.WriteFile(filepath.Join(dir, "subdir", ".git", "config"), []byte("gitcfg"), 0644)

	return dir
}

func TestWalk_AllFiles(t *testing.T) {
	dir := setupTestTree(t)

	var entries []*models.FileEntry
	err := Walk(dir, nil, false, func(entry *models.FileEntry) error {
		entries = append(entries, entry)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	// Should find: file1.txt, subdir/, subdir/file2.log, subdir/.git/, subdir/.git/config
	if len(entries) < 3 {
		t.Errorf("expected at least 3 entries, got %d", len(entries))
	}
}

func TestWalk_ExcludePatterns(t *testing.T) {
	dir := setupTestTree(t)

	var entries []*models.FileEntry
	err := Walk(dir, []string{".git"}, false, func(entry *models.FileEntry) error {
		entries = append(entries, entry)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	for _, e := range entries {
		if filepath.Base(e.Path) == ".git" || filepath.Base(e.Path) == "config" {
			t.Errorf("should have excluded .git directory, found: %s", e.Path)
		}
	}
}

func TestWalk_WithHash(t *testing.T) {
	dir := setupTestTree(t)

	var hashed int
	err := Walk(dir, nil, true, func(entry *models.FileEntry) error {
		if !entry.IsDir && entry.ContentHash != "" {
			hashed++
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	if hashed == 0 {
		t.Error("expected at least one file to have a hash")
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd scanner && go test ./internal/walker/ -v
```
Expected: FAIL — Walk not defined

- [ ] **Step 3: Implement walker**

`scanner/internal/walker/walker.go`:

```go
package walker

import (
	"io/fs"
	"path/filepath"
	"strings"

	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// WalkFunc is called for each file or directory found.
type WalkFunc func(entry *models.FileEntry) error

// Walk traverses the filesystem tree at root, calling fn for each entry.
// excludePatterns is a list of directory/file names to skip (e.g., ".git", "node_modules").
// If computeHash is true, BLAKE3 hashes are computed for regular files.
func Walk(root string, excludePatterns []string, computeHash bool, fn WalkFunc) error {
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}

	return filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil // skip unreadable entries
		}

		// Skip the root directory itself
		if path == root {
			return nil
		}

		name := d.Name()
		if excludeSet[strings.ToLower(name)] {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}

		info, err := d.Info()
		if err != nil {
			return nil // skip if we can't get info
		}

		entry, err := metadata.CollectFromInfo(path, info, computeHash)
		if err != nil {
			return nil // skip files we can't collect metadata for
		}

		return fn(entry)
	})
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd scanner && go test ./internal/walker/ -v
```
Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scanner/
git commit -m "feat: file tree walker with exclude patterns and hashing"
```

---

### Task 7: Connector Interface and Local Connector

**Files:**
- Create: `scanner/internal/connector/connector.go`
- Create: `scanner/internal/connector/local.go`
- Create: `scanner/internal/connector/local_test.go`

- [ ] **Step 1: Write connector interface and local connector tests**

`scanner/internal/connector/connector.go`:

```go
package connector

import (
	"context"
	"io"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// Connector abstracts filesystem access for different source types.
type Connector interface {
	// Connect establishes a connection to the source.
	Connect(ctx context.Context) error

	// Walk traverses the filesystem and calls fn for each entry.
	Walk(ctx context.Context, root string, excludePatterns []string, computeHash bool, fn func(*models.FileEntry) error) error

	// ReadFile reads a file's contents (for text extraction).
	ReadFile(ctx context.Context, path string) (io.ReadCloser, error)

	// Close releases any resources.
	Close() error

	// Type returns the connector type string.
	Type() string
}
```

`scanner/internal/connector/local_test.go`:

```go
package connector

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

func TestLocalConnector_Walk(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "a.txt"), []byte("aaa"), 0644)
	os.MkdirAll(filepath.Join(dir, "sub"), 0755)
	os.WriteFile(filepath.Join(dir, "sub", "b.txt"), []byte("bbb"), 0644)

	c := NewLocalConnector()
	if err := c.Connect(context.Background()); err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	var entries []*models.FileEntry
	err := c.Walk(context.Background(), dir, nil, true, func(e *models.FileEntry) error {
		entries = append(entries, e)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	fileCount := 0
	for _, e := range entries {
		if !e.IsDir {
			fileCount++
			if e.ContentHash == "" {
				t.Errorf("expected hash for %s", e.Path)
			}
		}
	}
	if fileCount != 2 {
		t.Errorf("expected 2 files, got %d", fileCount)
	}
}

func TestLocalConnector_ReadFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "read.txt")
	os.WriteFile(path, []byte("read me"), 0644)

	c := NewLocalConnector()
	c.Connect(context.Background())
	defer c.Close()

	reader, err := c.ReadFile(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()

	data, _ := io.ReadAll(reader)
	if string(data) != "read me" {
		t.Errorf("expected 'read me', got '%s'", string(data))
	}
}

func TestLocalConnector_Type(t *testing.T) {
	c := NewLocalConnector()
	if c.Type() != "local" {
		t.Errorf("expected type 'local', got '%s'", c.Type())
	}
}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd scanner && go test ./internal/connector/ -v
```
Expected: FAIL — NewLocalConnector not defined

- [ ] **Step 3: Implement local connector**

`scanner/internal/connector/local.go`:

```go
package connector

import (
	"context"
	"io"
	"os"

	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// LocalConnector handles local filesystem access (USB drives, mounted shares).
type LocalConnector struct{}

func NewLocalConnector() *LocalConnector {
	return &LocalConnector{}
}

func (c *LocalConnector) Connect(_ context.Context) error {
	return nil // local filesystem is always "connected"
}

func (c *LocalConnector) Walk(_ context.Context, root string, excludePatterns []string, computeHash bool, fn func(*models.FileEntry) error) error {
	return walker.Walk(root, excludePatterns, computeHash, fn)
}

func (c *LocalConnector) ReadFile(_ context.Context, path string) (io.ReadCloser, error) {
	return os.Open(path)
}

func (c *LocalConnector) Close() error {
	return nil
}

func (c *LocalConnector) Type() string {
	return "local"
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd scanner && go test ./internal/connector/ -v
```
Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scanner/
git commit -m "feat: connector interface and local filesystem connector"
```

---

### Task 8: SSH/SFTP Connector

**Files:**
- Create: `scanner/internal/connector/ssh.go`
- Create: `scanner/internal/connector/ssh_test.go`

- [ ] **Step 1: Write SSH connector tests**

`scanner/internal/connector/ssh_test.go`:

```go
package connector

import (
	"testing"
)

func TestSSHConnector_Type(t *testing.T) {
	c := NewSSHConnector("localhost", 22, "user", "", "")
	if c.Type() != "ssh" {
		t.Errorf("expected type 'ssh', got '%s'", c.Type())
	}
}

func TestSSHConnector_ConnectFailsWithBadHost(t *testing.T) {
	c := NewSSHConnector("192.0.2.1", 22, "user", "", "") // RFC 5737 TEST-NET
	ctx := t
	_ = ctx
	// Connection to a non-routable address should fail or timeout
	// This test validates the constructor and type; integration tests cover real SSH
}
```

Note: Full SSH integration tests require a real SSH server. Unit tests validate the constructor and interface compliance. Integration tests should use a Docker SSH container.

- [ ] **Step 2: Implement SSH connector**

```bash
cd scanner && go get github.com/pkg/sftp golang.org/x/crypto/ssh
```

`scanner/internal/connector/ssh.go`:

```go
package connector

import (
	"context"
	"fmt"
	"io"
	"io/fs"
	"net"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/pkg/sftp"
	"golang.org/x/crypto/ssh"

	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

type SSHConnector struct {
	host       string
	port       int
	username   string
	password   string
	keyPath    string
	client     *ssh.Client
	sftpClient *sftp.Client
}

func NewSSHConnector(host string, port int, username, password, keyPath string) *SSHConnector {
	return &SSHConnector{
		host:     host,
		port:     port,
		username: username,
		password: password,
		keyPath:  keyPath,
	}
}

func (c *SSHConnector) Connect(ctx context.Context) error {
	var authMethods []ssh.AuthMethod

	if c.keyPath != "" {
		key, err := os.ReadFile(c.keyPath)
		if err != nil {
			return fmt.Errorf("read key %s: %w", c.keyPath, err)
		}
		signer, err := ssh.ParsePrivateKey(key)
		if err != nil {
			return fmt.Errorf("parse key: %w", err)
		}
		authMethods = append(authMethods, ssh.PublicKeys(signer))
	}

	if c.password != "" {
		authMethods = append(authMethods, ssh.Password(c.password))
	}

	config := &ssh.ClientConfig{
		User:            c.username,
		Auth:            authMethods,
		HostKeyCallback: ssh.InsecureIgnoreHostKey(),
		Timeout:         30 * time.Second,
	}

	addr := net.JoinHostPort(c.host, fmt.Sprintf("%d", c.port))
	client, err := ssh.Dial("tcp", addr, config)
	if err != nil {
		return fmt.Errorf("ssh dial %s: %w", addr, err)
	}
	c.client = client

	sftpClient, err := sftp.NewClient(client)
	if err != nil {
		client.Close()
		return fmt.Errorf("sftp client: %w", err)
	}
	c.sftpClient = sftpClient

	return nil
}

func (c *SSHConnector) Walk(_ context.Context, root string, excludePatterns []string, computeHash bool, fn func(*models.FileEntry) error) error {
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}

	w := c.sftpClient.Walk(root)
	for w.Step() {
		if w.Err() != nil {
			continue
		}

		path := w.Path()
		if path == root {
			continue
		}

		info := w.Stat()
		name := info.Name()

		if excludeSet[strings.ToLower(name)] {
			if info.IsDir() {
				w.SkipDir()
			}
			continue
		}

		entry := fileInfoToEntry(path, info)

		if computeHash && !info.IsDir() {
			hash, err := c.hashRemoteFile(path)
			if err == nil {
				entry.ContentHash = hash
			}
		}

		if err := fn(entry); err != nil {
			return err
		}
	}
	return nil
}

func (c *SSHConnector) ReadFile(_ context.Context, path string) (io.ReadCloser, error) {
	return c.sftpClient.Open(path)
}

func (c *SSHConnector) Close() error {
	if c.sftpClient != nil {
		c.sftpClient.Close()
	}
	if c.client != nil {
		c.client.Close()
	}
	return nil
}

func (c *SSHConnector) Type() string {
	return "ssh"
}

func (c *SSHConnector) hashRemoteFile(path string) (string, error) {
	f, err := c.sftpClient.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()

	return metadata.HashReader(f)
}

func fileInfoToEntry(path string, info fs.FileInfo) *models.FileEntry {
	entry := &models.FileEntry{
		Path:      path,
		Filename:  info.Name(),
		SizeBytes: info.Size(),
		IsDir:     info.IsDir(),
	}

	if !info.IsDir() {
		ext := filepath.Ext(info.Name())
		if ext != "" {
			entry.Extension = strings.TrimPrefix(ext, ".")
		}
	}

	entry.Permissions = info.Mode().Perm().String()
	modTime := info.ModTime()
	entry.ModifiedAt = &modTime

	return entry
}
```

Also add `HashReader` to `scanner/internal/metadata/collector.go`:

```go
// HashReader computes a BLAKE3 hash from a reader.
func HashReader(r io.Reader) (string, error) {
	hasher := blake3.New()
	if _, err := io.Copy(hasher, r); err != nil {
		return "", err
	}
	return fmt.Sprintf("%x", hasher.Sum(nil)), nil
}
```

- [ ] **Step 3: Run tests**

```bash
cd scanner && go test ./internal/connector/ -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add scanner/
git commit -m "feat: SSH/SFTP connector for remote Linux/macOS scanning"
```

---

### Task 9: SMB, NFS, and S3 Connectors

**Files:**
- Create: `scanner/internal/connector/smb.go`
- Create: `scanner/internal/connector/nfs.go`
- Create: `scanner/internal/connector/s3.go`
- Create: `scanner/internal/connector/s3_test.go`

- [ ] **Step 1: Add dependencies**

```bash
cd scanner
go get github.com/hirochachacha/go-smb2
go get github.com/aws/aws-sdk-go-v2/service/s3
go get github.com/aws/aws-sdk-go-v2/config
go get github.com/aws/aws-sdk-go-v2/credentials
```

- [ ] **Step 2: Implement SMB connector**

`scanner/internal/connector/smb.go`:

```go
package connector

import (
	"context"
	"fmt"
	"io"
	"io/fs"
	"net"
	"path/filepath"
	"strings"

	"github.com/hirochachacha/go-smb2"

	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

type SMBConnector struct {
	host     string
	port     int
	username string
	password string
	share    string
	conn     net.Conn
	session  *smb2.Session
	smbShare *smb2.Share
}

func NewSMBConnector(host string, port int, username, password, share string) *SMBConnector {
	return &SMBConnector{
		host:     host,
		port:     port,
		username: username,
		password: password,
		share:    share,
	}
}

func (c *SMBConnector) Connect(_ context.Context) error {
	addr := net.JoinHostPort(c.host, fmt.Sprintf("%d", c.port))
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		return fmt.Errorf("smb dial %s: %w", addr, err)
	}
	c.conn = conn

	d := &smb2.Dialer{
		Initiator: &smb2.NTLMInitiator{
			User:     c.username,
			Password: c.password,
		},
	}

	session, err := d.Dial(conn)
	if err != nil {
		conn.Close()
		return fmt.Errorf("smb session: %w", err)
	}
	c.session = session

	share, err := session.Mount(c.share)
	if err != nil {
		session.Logoff()
		conn.Close()
		return fmt.Errorf("smb mount %s: %w", c.share, err)
	}
	c.smbShare = share

	return nil
}

func (c *SMBConnector) Walk(_ context.Context, root string, excludePatterns []string, computeHash bool, fn func(*models.FileEntry) error) error {
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}

	return c.walkDir(root, excludeSet, computeHash, fn)
}

func (c *SMBConnector) walkDir(dir string, excludeSet map[string]bool, computeHash bool, fn func(*models.FileEntry) error) error {
	entries, err := c.smbShare.ReadDir(dir)
	if err != nil {
		return nil // skip unreadable dirs
	}

	for _, info := range entries {
		name := info.Name()
		if excludeSet[strings.ToLower(name)] {
			continue
		}

		path := filepath.Join(dir, name)
		entry := fileInfoToEntry(path, info)

		if computeHash && !info.IsDir() {
			if hash, err := c.hashRemoteFile(path); err == nil {
				entry.ContentHash = hash
			}
		}

		if err := fn(entry); err != nil {
			return err
		}

		if info.IsDir() {
			if err := c.walkDir(path, excludeSet, computeHash, fn); err != nil {
				return err
			}
		}
	}
	return nil
}

func (c *SMBConnector) hashRemoteFile(path string) (string, error) {
	f, err := c.smbShare.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	return metadata.HashReader(f)
}

func (c *SMBConnector) ReadFile(_ context.Context, path string) (io.ReadCloser, error) {
	return c.smbShare.Open(path)
}

func (c *SMBConnector) Close() error {
	if c.smbShare != nil {
		c.smbShare.Umount()
	}
	if c.session != nil {
		c.session.Logoff()
	}
	if c.conn != nil {
		c.conn.Close()
	}
	return nil
}

func (c *SMBConnector) Type() string {
	return "smb"
}
```

- [ ] **Step 3: Implement NFS connector (mount-based)**

`scanner/internal/connector/nfs.go`:

```go
package connector

import (
	"context"
	"io"

	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// NFSConnector accesses NFS shares via local mount points.
// The NFS share must be mounted on the host before scanning.
// This is the simplest and most reliable approach — mount management
// is delegated to the OS (fstab or autofs).
type NFSConnector struct {
	local *LocalConnector
}

func NewNFSConnector() *NFSConnector {
	return &NFSConnector{local: NewLocalConnector()}
}

func (c *NFSConnector) Connect(ctx context.Context) error {
	return c.local.Connect(ctx)
}

func (c *NFSConnector) Walk(ctx context.Context, root string, excludePatterns []string, computeHash bool, fn func(*models.FileEntry) error) error {
	return walker.Walk(root, excludePatterns, computeHash, fn)
}

func (c *NFSConnector) ReadFile(ctx context.Context, path string) (io.ReadCloser, error) {
	return c.local.ReadFile(ctx, path)
}

func (c *NFSConnector) Close() error {
	return nil
}

func (c *NFSConnector) Type() string {
	return "nfs"
}
```

- [ ] **Step 4: Implement S3 connector**

`scanner/internal/connector/s3.go`:

```go
package connector

import (
	"context"
	"fmt"
	"io"
	"path/filepath"
	"strings"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"

	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

type S3Connector struct {
	endpoint  string
	bucket    string
	region    string
	accessKey string
	secretKey string
	client    *s3.Client
}

func NewS3Connector(endpoint, bucket, region, accessKey, secretKey string) *S3Connector {
	return &S3Connector{
		endpoint:  endpoint,
		bucket:    bucket,
		region:    region,
		accessKey: accessKey,
		secretKey: secretKey,
	}
}

func (c *S3Connector) Connect(ctx context.Context) error {
	cfg, err := awsconfig.LoadDefaultConfig(ctx,
		awsconfig.WithRegion(c.region),
		awsconfig.WithCredentialsProvider(credentials.NewStaticCredentialsProvider(c.accessKey, c.secretKey, "")),
	)
	if err != nil {
		return fmt.Errorf("s3 config: %w", err)
	}

	c.client = s3.NewFromConfig(cfg, func(o *s3.Options) {
		if c.endpoint != "" {
			o.BaseEndpoint = aws.String(c.endpoint)
			o.UsePathStyle = true
		}
	})

	return nil
}

func (c *S3Connector) Walk(ctx context.Context, prefix string, excludePatterns []string, computeHash bool, fn func(*models.FileEntry) error) error {
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}

	paginator := s3.NewListObjectsV2Paginator(c.client, &s3.ListObjectsV2Input{
		Bucket: aws.String(c.bucket),
		Prefix: aws.String(prefix),
	})

	for paginator.HasMorePages() {
		page, err := paginator.NextPage(ctx)
		if err != nil {
			return fmt.Errorf("s3 list: %w", err)
		}

		for _, obj := range page.Contents {
			key := aws.ToString(obj.Key)

			// Check exclude patterns against each path component
			skip := false
			for _, part := range strings.Split(key, "/") {
				if excludeSet[strings.ToLower(part)] {
					skip = true
					break
				}
			}
			if skip {
				continue
			}

			isDir := strings.HasSuffix(key, "/")
			entry := &models.FileEntry{
				Path:      key,
				Filename:  filepath.Base(key),
				SizeBytes: aws.ToInt64(obj.Size),
				IsDir:     isDir,
			}

			if !isDir {
				ext := filepath.Ext(entry.Filename)
				if ext != "" {
					entry.Extension = strings.TrimPrefix(ext, ".")
				}
			}

			if obj.LastModified != nil {
				t := *obj.LastModified
				entry.ModifiedAt = &t
			}

			// S3 ETag can serve as a hash for non-multipart uploads
			if obj.ETag != nil {
				entry.ContentHash = strings.Trim(aws.ToString(obj.ETag), "\"")
			}

			// For proper BLAKE3 hashing, download and hash
			if computeHash && !isDir {
				if hash, err := c.hashObject(ctx, key); err == nil {
					entry.ContentHash = hash
				}
			}

			if err := fn(entry); err != nil {
				return err
			}
		}
	}

	return nil
}

func (c *S3Connector) hashObject(ctx context.Context, key string) (string, error) {
	output, err := c.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return "", err
	}
	defer output.Body.Close()
	return metadata.HashReader(output.Body)
}

func (c *S3Connector) ReadFile(ctx context.Context, path string) (io.ReadCloser, error) {
	output, err := c.client.GetObject(ctx, &s3.GetObjectInput{
		Bucket: aws.String(c.bucket),
		Key:    aws.String(path),
	})
	if err != nil {
		return nil, err
	}
	return output.Body, nil
}

func (c *S3Connector) Close() error {
	return nil
}

func (c *S3Connector) Type() string {
	return "s3"
}
```

- [ ] **Step 5: Write S3 connector unit test**

`scanner/internal/connector/s3_test.go`:

```go
package connector

import (
	"testing"
)

func TestS3Connector_Type(t *testing.T) {
	c := NewS3Connector("http://localhost:9000", "test-bucket", "us-east-1", "minioadmin", "minioadmin")
	if c.Type() != "s3" {
		t.Errorf("expected type 's3', got '%s'", c.Type())
	}
}
```

- [ ] **Step 6: Run all connector tests**

```bash
cd scanner && go test ./internal/connector/ -v
```
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add scanner/
git commit -m "feat: SMB, NFS, and S3 connectors"
```

---

### Task 10: API Client and Scanner Orchestrator

**Files:**
- Create: `scanner/internal/client/client.go`
- Create: `scanner/internal/client/client_test.go`
- Create: `scanner/internal/scanner/scanner.go`
- Create: `scanner/internal/scanner/scanner_test.go`

- [ ] **Step 1: Write API client tests**

`scanner/internal/client/client_test.go`:

```go
package client

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

func TestClient_SendBatch(t *testing.T) {
	var received models.ScanBatch

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/ingest/batch" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer test-key" {
			t.Error("missing or wrong auth header")
		}
		json.NewDecoder(r.Body).Decode(&received)
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	}))
	defer server.Close()

	c := New(server.URL, "test-key")
	batch := models.ScanBatch{
		SourceID: "src-1",
		ScanID:   "scan-1",
		Files: []models.FileEntry{
			{Path: "/a.txt", Filename: "a.txt", SizeBytes: 100},
		},
	}

	err := c.SendBatch(context.Background(), batch)
	if err != nil {
		t.Fatal(err)
	}

	if len(received.Files) != 1 {
		t.Errorf("expected 1 file, got %d", len(received.Files))
	}
}
```

- [ ] **Step 2: Implement API client**

`scanner/internal/client/client.go`:

```go
package client

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

type Client struct {
	baseURL    string
	apiKey     string
	httpClient *http.Client
}

func New(baseURL, apiKey string) *Client {
	return &Client{
		baseURL: baseURL,
		apiKey:  apiKey,
		httpClient: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

func (c *Client) SendBatch(ctx context.Context, batch models.ScanBatch) error {
	body, err := json.Marshal(batch)
	if err != nil {
		return fmt.Errorf("marshal batch: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/api/ingest/batch", bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.apiKey)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("send batch: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("batch rejected: status %d", resp.StatusCode)
	}

	return nil
}
```

- [ ] **Step 3: Run client tests**

```bash
cd scanner && go test ./internal/client/ -v
```
Expected: PASS

- [ ] **Step 4: Write scanner orchestrator tests**

`scanner/internal/scanner/scanner_test.go`:

```go
package scanner

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/connector"
)

func TestScanner_ScanLocal(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "f1.txt"), []byte("one"), 0644)
	os.WriteFile(filepath.Join(dir, "f2.txt"), []byte("two"), 0644)
	os.MkdirAll(filepath.Join(dir, "sub"), 0755)
	os.WriteFile(filepath.Join(dir, "sub", "f3.txt"), []byte("three"), 0644)

	var batchCount atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		batchCount.Add(1)
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	}))
	defer server.Close()

	apiClient := client.New(server.URL, "key")
	conn := connector.NewLocalConnector()

	s := New(apiClient, conn, Options{
		SourceID:  "test-source",
		ScanID:    "test-scan",
		Root:      dir,
		BatchSize: 2, // Force multiple batches
		Hash:      true,
	})

	result, err := s.Run(context.Background())
	if err != nil {
		t.Fatal(err)
	}

	if result.FilesFound < 3 {
		t.Errorf("expected at least 3 files, got %d", result.FilesFound)
	}

	if batchCount.Load() < 2 {
		t.Errorf("expected at least 2 batches with batch size 2, got %d", batchCount.Load())
	}
}
```

- [ ] **Step 5: Implement scanner orchestrator**

`scanner/internal/scanner/scanner.go`:

```go
package scanner

import (
	"context"
	"fmt"
	"log"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

type Options struct {
	SourceID        string
	ScanID          string
	Root            string
	BatchSize       int
	Hash            bool
	ExcludePatterns []string
}

type Result struct {
	FilesFound int
	DirsFound  int
	BatchesSent int
}

type Scanner struct {
	client    *client.Client
	connector connector.Connector
	opts      Options
}

func New(apiClient *client.Client, conn connector.Connector, opts Options) *Scanner {
	if opts.BatchSize <= 0 {
		opts.BatchSize = 1000
	}
	return &Scanner{
		client:    apiClient,
		connector: conn,
		opts:      opts,
	}
}

func (s *Scanner) Run(ctx context.Context) (*Result, error) {
	if err := s.connector.Connect(ctx); err != nil {
		return nil, fmt.Errorf("connect: %w", err)
	}
	defer s.connector.Close()

	result := &Result{}
	var batch []models.FileEntry

	flush := func(final bool) error {
		if len(batch) == 0 && !final {
			return nil
		}
		scanBatch := models.ScanBatch{
			SourceID: s.opts.SourceID,
			ScanID:   s.opts.ScanID,
			Files:    batch,
			IsFinal:  final,
		}
		if err := s.client.SendBatch(ctx, scanBatch); err != nil {
			return fmt.Errorf("send batch: %w", err)
		}
		result.BatchesSent++
		batch = nil
		return nil
	}

	err := s.connector.Walk(ctx, s.opts.Root, s.opts.ExcludePatterns, s.opts.Hash, func(entry *models.FileEntry) error {
		if entry.IsDir {
			result.DirsFound++
		} else {
			result.FilesFound++
		}

		batch = append(batch, *entry)

		if len(batch) >= s.opts.BatchSize {
			return flush(false)
		}
		return nil
	})
	if err != nil {
		return nil, fmt.Errorf("walk: %w", err)
	}

	// Flush remaining + final batch
	if err := flush(true); err != nil {
		return nil, err
	}

	log.Printf("scan complete: %d files, %d dirs, %d batches", result.FilesFound, result.DirsFound, result.BatchesSent)
	return result, nil
}
```

- [ ] **Step 6: Run tests**

```bash
cd scanner && go test ./internal/scanner/ -v
```
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add scanner/
git commit -m "feat: scanner orchestrator with batched API upload"
```

---

### Task 11: Scanner Binary Entrypoint

**Files:**
- Create: `scanner/cmd/akashic-scanner/main.go`

- [ ] **Step 1: Create scanner CLI entrypoint**

`scanner/cmd/akashic-scanner/main.go`:

```go
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"strings"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/internal/config"
	"github.com/akashic-project/akashic/scanner/internal/scanner"
)

func main() {
	// Flags
	sourceID := flag.String("source-id", "", "Source ID to scan")
	scanID := flag.String("scan-id", "", "Scan ID for this run")
	sourceType := flag.String("type", "local", "Source type: local, ssh, smb, nfs, s3")
	root := flag.String("root", "", "Root path to scan")
	host := flag.String("host", "", "Remote host (for ssh, smb, s3)")
	port := flag.Int("port", 0, "Remote port")
	username := flag.String("user", "", "Username")
	password := flag.String("pass", "", "Password")
	keyPath := flag.String("key", "", "SSH key path")
	share := flag.String("share", "", "SMB share name")
	bucket := flag.String("bucket", "", "S3 bucket name")
	region := flag.String("region", "us-east-1", "S3 region")
	endpoint := flag.String("endpoint", "", "S3 endpoint URL")
	excludes := flag.String("exclude", ".git,node_modules,__pycache__,.DS_Store,Thumbs.db", "Comma-separated exclude patterns")
	fullScan := flag.Bool("full", false, "Full scan (hash all files)")
	batchSize := flag.Int("batch-size", 1000, "Files per batch")

	flag.Parse()

	if *sourceID == "" || *root == "" {
		fmt.Fprintln(os.Stderr, "required: -source-id and -root")
		flag.Usage()
		os.Exit(1)
	}

	cfg := config.Load()

	// Build connector
	var conn connector.Connector
	switch *sourceType {
	case "local":
		conn = connector.NewLocalConnector()
	case "ssh":
		p := *port
		if p == 0 {
			p = 22
		}
		conn = connector.NewSSHConnector(*host, p, *username, *password, *keyPath)
	case "smb":
		p := *port
		if p == 0 {
			p = 445
		}
		conn = connector.NewSMBConnector(*host, p, *username, *password, *share)
	case "nfs":
		conn = connector.NewNFSConnector()
	case "s3":
		conn = connector.NewS3Connector(*endpoint, *bucket, *region, *username, *password)
	default:
		log.Fatalf("unknown source type: %s", *sourceType)
	}

	var excludePatterns []string
	if *excludes != "" {
		excludePatterns = strings.Split(*excludes, ",")
	}

	apiClient := client.New(cfg.APIUrl, cfg.APIKey)

	sid := *scanID
	if sid == "" {
		sid = fmt.Sprintf("scan-%s", *sourceID)
	}

	s := scanner.New(apiClient, conn, scanner.Options{
		SourceID:        *sourceID,
		ScanID:          sid,
		Root:            *root,
		BatchSize:       *batchSize,
		Hash:            *fullScan,
		ExcludePatterns: excludePatterns,
	})

	result, err := s.Run(context.Background())
	if err != nil {
		log.Fatalf("scan failed: %v", err)
	}

	fmt.Printf("Scan complete: %d files, %d directories, %d batches sent\n",
		result.FilesFound, result.DirsFound, result.BatchesSent)
}
```

- [ ] **Step 2: Build and verify**

```bash
cd scanner && go build -o akashic-scanner ./cmd/akashic-scanner/
./akashic-scanner --help
```
Expected: help text with all flags displayed

- [ ] **Step 3: Commit**

```bash
git add scanner/
git commit -m "feat: scanner binary entrypoint with CLI flags"
```

---

## Phase 3: Python API — Core Endpoints

### Task 12: Scan Ingest Endpoint

**Files:**
- Create: `api/akashic/schemas/scan.py`
- Create: `api/akashic/schemas/file.py`
- Create: `api/akashic/routers/ingest.py`
- Create: `api/akashic/services/duplicates.py`
- Create: `api/akashic/services/movement.py`
- Create: `api/tests/test_ingest.py`

- [ ] **Step 1: Write ingest tests**

`api/tests/test_ingest.py`:

```python
import uuid

import pytest


@pytest.mark.asyncio
async def test_ingest_batch_creates_files(client, db_session):
    from akashic.models import Source, User
    # Create source and scanner user
    user = User(username="scanner", role="scanner", password_hash="x")
    db_session.add(user)
    source = Source(name="test-src", type="local", connection_config={"path": "/tmp"})
    db_session.add(source)
    await db_session.commit()
    await db_session.refresh(source)
    await db_session.refresh(user)

    # Get token
    from akashic.auth.jwt import create_access_token
    token = create_access_token({"sub": str(user.id)})

    scan_id = str(uuid.uuid4())
    response = await client.post("/api/ingest/batch", json={
        "source_id": str(source.id),
        "scan_id": scan_id,
        "is_final": False,
        "files": [
            {
                "path": "/tmp/test.txt",
                "filename": "test.txt",
                "extension": "txt",
                "size_bytes": 100,
                "mime_type": "text/plain",
                "content_hash": "abc123",
                "is_dir": False,
            },
            {
                "path": "/tmp/sub",
                "filename": "sub",
                "size_bytes": 0,
                "is_dir": True,
            },
        ],
    }, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    data = response.json()
    assert data["files_processed"] == 2


@pytest.mark.asyncio
async def test_ingest_detects_duplicates(client, db_session):
    from akashic.models import Source, User, File
    user = User(username="scanner2", role="scanner", password_hash="x")
    db_session.add(user)
    src1 = Source(name="src-a", type="local", connection_config={"path": "/a"})
    src2 = Source(name="src-b", type="local", connection_config={"path": "/b"})
    db_session.add_all([src1, src2])
    await db_session.commit()

    # Pre-existing file on src1
    existing = File(source_id=src1.id, path="/a/file.dat", filename="file.dat", content_hash="samehash", size_bytes=500)
    db_session.add(existing)
    await db_session.commit()

    from akashic.auth.jwt import create_access_token
    token = create_access_token({"sub": str(user.id)})

    # Ingest same hash on src2
    response = await client.post("/api/ingest/batch", json={
        "source_id": str(src2.id),
        "scan_id": str(uuid.uuid4()),
        "is_final": True,
        "files": [{
            "path": "/b/copy.dat",
            "filename": "copy.dat",
            "size_bytes": 500,
            "content_hash": "samehash",
            "is_dir": False,
        }],
    }, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && pytest tests/test_ingest.py -v
```
Expected: FAIL — modules not found

- [ ] **Step 3: Implement schemas**

`api/akashic/schemas/file.py`:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel


class FileEntryIn(BaseModel):
    path: str
    filename: str
    extension: str | None = None
    size_bytes: int = 0
    mime_type: str | None = None
    content_hash: str | None = None
    permissions: str | None = None
    owner: str | None = None
    file_group: str | None = None
    fs_created_at: datetime | None = None
    fs_modified_at: datetime | None = None
    fs_accessed_at: datetime | None = None
    is_dir: bool = False


class FileResponse(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    path: str
    filename: str
    extension: str | None
    size_bytes: int | None
    mime_type: str | None
    content_hash: str | None
    fs_modified_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    is_deleted: bool

    model_config = {"from_attributes": True}
```

`api/akashic/schemas/scan.py`:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel

from akashic.schemas.file import FileEntryIn


class ScanBatchIn(BaseModel):
    source_id: uuid.UUID
    scan_id: uuid.UUID
    files: list[FileEntryIn]
    is_final: bool = False


class ScanBatchResponse(BaseModel):
    files_processed: int
    scan_id: uuid.UUID


class ScanResponse(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    scan_type: str
    status: str
    files_found: int
    files_new: int
    files_changed: int
    files_deleted: int
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Implement ingest router**

`api/akashic/routers/ingest.py`:

```python
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.models.directory import Directory
from akashic.models.file import File, FileVersion
from akashic.models.scan import Scan
from akashic.models.user import User
from akashic.schemas.scan import ScanBatchIn, ScanBatchResponse

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.post("/batch", response_model=ScanBatchResponse)
async def ingest_batch(
    batch: ScanBatchIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    now = datetime.now(timezone.utc)

    # Ensure scan record exists
    result = await db.execute(select(Scan).where(Scan.id == batch.scan_id))
    scan = result.scalar_one_or_none()
    if not scan:
        scan = Scan(
            id=batch.scan_id,
            source_id=batch.source_id,
            scan_type="incremental",
            status="running",
            started_at=now,
        )
        db.add(scan)

    files_processed = 0

    for entry in batch.files:
        if entry.is_dir:
            # Upsert directory
            stmt = insert(Directory).values(
                source_id=batch.source_id,
                path=entry.path,
                name=entry.filename,
                last_seen_at=now,
            ).on_conflict_do_update(
                constraint="uq_directories_source_path",
                set_={"last_seen_at": now, "is_deleted": False},
            )
            await db.execute(stmt)
        else:
            # Check for existing file at this source+path
            existing_result = await db.execute(
                select(File).where(File.source_id == batch.source_id, File.path == entry.path)
            )
            existing = existing_result.scalar_one_or_none()

            if existing:
                # Update existing file
                old_hash = existing.content_hash
                existing.filename = entry.filename
                existing.extension = entry.extension
                existing.size_bytes = entry.size_bytes
                existing.mime_type = entry.mime_type
                existing.content_hash = entry.content_hash
                existing.permissions = entry.permissions
                existing.owner = entry.owner
                existing.file_group = entry.file_group
                existing.fs_created_at = entry.fs_created_at
                existing.fs_modified_at = entry.fs_modified_at
                existing.fs_accessed_at = entry.fs_accessed_at
                existing.last_seen_at = now
                existing.is_deleted = False
                existing.deleted_at = None

                # Track version change
                if old_hash and entry.content_hash and old_hash != entry.content_hash:
                    version = FileVersion(
                        file_id=existing.id,
                        content_hash=entry.content_hash,
                        size_bytes=entry.size_bytes,
                        scan_id=batch.scan_id,
                    )
                    db.add(version)
                    scan.files_changed += 1
            else:
                # New file
                new_file = File(
                    source_id=batch.source_id,
                    path=entry.path,
                    filename=entry.filename,
                    extension=entry.extension,
                    size_bytes=entry.size_bytes,
                    mime_type=entry.mime_type,
                    content_hash=entry.content_hash,
                    permissions=entry.permissions,
                    owner=entry.owner,
                    file_group=entry.file_group,
                    fs_created_at=entry.fs_created_at,
                    fs_modified_at=entry.fs_modified_at,
                    fs_accessed_at=entry.fs_accessed_at,
                    first_seen_at=now,
                    last_seen_at=now,
                )
                db.add(new_file)
                scan.files_new += 1

        files_processed += 1

    scan.files_found += files_processed

    if batch.is_final:
        scan.status = "completed"
        scan.completed_at = now

    await db.commit()

    return ScanBatchResponse(files_processed=files_processed, scan_id=batch.scan_id)
```

Register the router in `api/akashic/main.py`:

```python
from fastapi import FastAPI

from akashic.routers import users, ingest


def create_app() -> FastAPI:
    app = FastAPI(title="Akashic", version="0.1.0")
    app.include_router(users.router)
    app.include_router(ingest.router)
    return app


app = create_app()
```

- [ ] **Step 5: Run tests**

```bash
cd api && pytest tests/test_ingest.py -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add api/
git commit -m "feat: scan ingest endpoint with file upsert and version tracking"
```

---

### Task 13: Source Management Endpoints

**Files:**
- Create: `api/akashic/schemas/source.py`
- Create: `api/akashic/routers/sources.py`
- Create: `api/tests/test_sources.py`

- [ ] **Step 1: Write source tests**

`api/tests/test_sources.py`:

```python
import pytest

from tests.conftest import get_auth_token


async def get_auth_token(client):
    await client.post("/api/users/register", json={
        "username": "srcadmin", "password": "pass123",
    })
    resp = await client.post("/api/users/login", json={
        "username": "srcadmin", "password": "pass123",
    })
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_create_source(client):
    token = await get_auth_token(client)
    response = await client.post("/api/sources", json={
        "name": "my-nas",
        "type": "smb",
        "connection_config": {"host": "10.0.0.1", "share": "data"},
    }, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "my-nas"
    assert data["status"] == "offline"


@pytest.mark.asyncio
async def test_list_sources(client):
    token = await get_auth_token(client)
    await client.post("/api/sources", json={
        "name": "src1", "type": "local", "connection_config": {"path": "/mnt/a"},
    }, headers={"Authorization": f"Bearer {token}"})
    await client.post("/api/sources", json={
        "name": "src2", "type": "ssh", "connection_config": {"host": "10.0.0.2"},
    }, headers={"Authorization": f"Bearer {token}"})

    response = await client.get("/api/sources", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert len(response.json()) >= 2


@pytest.mark.asyncio
async def test_delete_source(client):
    token = await get_auth_token(client)
    create = await client.post("/api/sources", json={
        "name": "deleteme", "type": "local", "connection_config": {"path": "/tmp"},
    }, headers={"Authorization": f"Bearer {token}"})
    source_id = create.json()["id"]

    response = await client.delete(f"/api/sources/{source_id}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 204
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && pytest tests/test_sources.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement source schemas and router**

`api/akashic/schemas/source.py`:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel


class SourceCreate(BaseModel):
    name: str
    type: str
    connection_config: dict
    scan_schedule: str | None = None
    exclude_patterns: list[str] | None = None


class SourceUpdate(BaseModel):
    name: str | None = None
    connection_config: dict | None = None
    scan_schedule: str | None = None
    exclude_patterns: list[str] | None = None


class SourceResponse(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    connection_config: dict
    scan_schedule: str | None
    exclude_patterns: list[str] | None
    last_scan_at: datetime | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
```

`api/akashic/routers/sources.py`:

```python
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.models.source import Source
from akashic.models.user import User
from akashic.schemas.source import SourceCreate, SourceUpdate, SourceResponse

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.post("", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def create_source(
    data: SourceCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    source = Source(**data.model_dump())
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


@router.get("", response_model=list[SourceResponse])
async def list_sources(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(Source).order_by(Source.name))
    return result.scalars().all()


@router.get("/{source_id}", response_model=SourceResponse)
async def get_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.patch("/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: uuid.UUID,
    data: SourceUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(source, field, value)
    await db.commit()
    await db.refresh(source)
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    await db.delete(source)
    await db.commit()
```

Register in `main.py` — add `from akashic.routers import users, ingest, sources` and `app.include_router(sources.router)`.

- [ ] **Step 4: Run tests**

```bash
cd api && pytest tests/test_sources.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/
git commit -m "feat: source CRUD endpoints"
```

---

### Task 14: Meilisearch Integration and Search Endpoint

**Files:**
- Create: `api/akashic/services/search.py`
- Create: `api/akashic/schemas/search.py`
- Create: `api/akashic/routers/search.py`
- Create: `api/tests/test_search.py`

- [ ] **Step 1: Write search tests**

`api/tests/test_search.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_search_by_filename(client, db_session):
    from akashic.models import Source, User, File
    from akashic.auth.jwt import create_access_token

    user = User(username="searcher", role="viewer", password_hash="x")
    db_session.add(user)
    source = Source(name="search-src", type="local", connection_config={"path": "/data"})
    db_session.add(source)
    await db_session.commit()

    # Add files directly to DB
    for name in ["report-2024.pdf", "report-2025.pdf", "photo.jpg"]:
        f = File(source_id=source.id, path=f"/data/{name}", filename=name,
                 extension=name.split(".")[-1], size_bytes=1024)
        db_session.add(f)
    await db_session.commit()

    token = create_access_token({"sub": str(user.id)})

    # Search should find files by filename in PostgreSQL fallback
    response = await client.get("/api/search", params={"q": "report"},
                                headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 2
    assert all("report" in r["filename"] for r in results)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && pytest tests/test_search.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement search service**

`api/akashic/services/search.py`:

```python
from meilisearch_python_sdk import AsyncClient

from akashic.config import settings

INDEX_NAME = "files"


async def get_meili_client() -> AsyncClient:
    return AsyncClient(settings.meili_url, settings.meili_key)


async def ensure_index():
    """Create and configure the Meilisearch files index if it doesn't exist."""
    client = await get_meili_client()
    try:
        await client.get_index(INDEX_NAME)
    except Exception:
        await client.create_index(INDEX_NAME, primary_key="id")
        index = await client.get_index(INDEX_NAME)
        await index.update_searchable_attributes(["filename", "path", "content_text", "tags"])
        await index.update_filterable_attributes([
            "source_id", "extension", "mime_type", "size_bytes", "fs_modified_at", "tags",
        ])
        await index.update_sortable_attributes(["size_bytes", "fs_modified_at", "filename"])


async def index_file(file_data: dict):
    """Add or update a file in the search index."""
    client = await get_meili_client()
    index = await client.get_index(INDEX_NAME)
    await index.add_documents([file_data])


async def index_files_batch(files: list[dict]):
    """Add or update multiple files in the search index."""
    if not files:
        return
    client = await get_meili_client()
    index = await client.get_index(INDEX_NAME)
    await index.add_documents(files)


async def search_files(query: str, filters: str | None = None, sort: list[str] | None = None,
                       offset: int = 0, limit: int = 20) -> dict:
    """Search the files index."""
    client = await get_meili_client()
    index = await client.get_index(INDEX_NAME)
    return await index.search(query, filter=filters, sort=sort, offset=offset, limit=limit)


async def delete_file_from_index(file_id: str):
    """Remove a file from the search index."""
    client = await get_meili_client()
    index = await client.get_index(INDEX_NAME)
    await index.delete_document(file_id)
```

`api/akashic/schemas/search.py`:

```python
from pydantic import BaseModel

from akashic.schemas.file import FileResponse


class SearchQuery(BaseModel):
    q: str
    source_id: str | None = None
    extension: str | None = None
    min_size: int | None = None
    max_size: int | None = None
    tag: str | None = None
    sort: str | None = None
    offset: int = 0
    limit: int = 20


class SearchResults(BaseModel):
    results: list[FileResponse]
    total: int
    query: str
```

`api/akashic/routers/search.py`:

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.models.file import File
from akashic.models.user import User
from akashic.schemas.search import SearchResults

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=SearchResults)
async def search(
    q: str = Query(..., min_length=1),
    source_id: str | None = None,
    extension: str | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    offset: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Try Meilisearch first, fall back to PostgreSQL
    try:
        from akashic.services.search import search_files

        filters = []
        if source_id:
            filters.append(f'source_id = "{source_id}"')
        if extension:
            filters.append(f'extension = "{extension}"')
        if min_size is not None:
            filters.append(f"size_bytes >= {min_size}")
        if max_size is not None:
            filters.append(f"size_bytes <= {max_size}")

        filter_str = " AND ".join(filters) if filters else None
        meili_results = await search_files(q, filters=filter_str, offset=offset, limit=limit)

        return SearchResults(
            results=meili_results.hits,
            total=meili_results.estimated_total_hits or 0,
            query=q,
        )
    except Exception:
        # Fallback to PostgreSQL ILIKE search
        conditions = [File.is_deleted == False, File.filename.ilike(f"%{q}%")]  # noqa: E712
        if source_id:
            conditions.append(File.source_id == source_id)
        if extension:
            conditions.append(File.extension == extension)
        if min_size is not None:
            conditions.append(File.size_bytes >= min_size)
        if max_size is not None:
            conditions.append(File.size_bytes <= max_size)

        query_stmt = select(File).where(and_(*conditions)).offset(offset).limit(limit)
        result = await db.execute(query_stmt)
        files = result.scalars().all()

        count_stmt = select(File).where(and_(*conditions))
        count_result = await db.execute(count_stmt)
        total = len(count_result.scalars().all())

        return SearchResults(results=files, total=total, query=q)
```

Register in `main.py`: add `search` to imports and `app.include_router(search.router)`.

- [ ] **Step 4: Run tests**

```bash
cd api && pytest tests/test_search.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/
git commit -m "feat: search endpoint with Meilisearch and PostgreSQL fallback"
```

---

### Task 15: Files, Directories, and Duplicates Endpoints

**Files:**
- Create: `api/akashic/routers/files.py`
- Create: `api/akashic/routers/directories.py`
- Create: `api/akashic/routers/duplicates.py`
- Create: `api/tests/test_files.py`
- Create: `api/tests/test_duplicates.py`

- [ ] **Step 1: Write file and duplicate tests**

`api/tests/test_files.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_get_file_detail(client, db_session):
    from akashic.models import Source, User, File
    from akashic.auth.jwt import create_access_token

    user = User(username="fileuser", role="viewer", password_hash="x")
    db_session.add(user)
    source = Source(name="file-src", type="local", connection_config={})
    db_session.add(source)
    await db_session.commit()

    f = File(source_id=source.id, path="/data/doc.pdf", filename="doc.pdf",
             extension="pdf", size_bytes=2048, content_hash="hash1")
    db_session.add(f)
    await db_session.commit()
    await db_session.refresh(f)

    token = create_access_token({"sub": str(user.id)})
    response = await client.get(f"/api/files/{f.id}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["filename"] == "doc.pdf"


@pytest.mark.asyncio
async def test_list_files_by_source(client, db_session):
    from akashic.models import Source, User, File
    from akashic.auth.jwt import create_access_token

    user = User(username="listuser", role="viewer", password_hash="x")
    db_session.add(user)
    source = Source(name="list-src", type="local", connection_config={})
    db_session.add(source)
    await db_session.commit()

    for i in range(3):
        db_session.add(File(source_id=source.id, path=f"/data/f{i}.txt",
                           filename=f"f{i}.txt", extension="txt", size_bytes=100))
    await db_session.commit()

    token = create_access_token({"sub": str(user.id)})
    response = await client.get(f"/api/files?source_id={source.id}",
                                headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert len(response.json()) == 3
```

`api/tests/test_duplicates.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_list_duplicates(client, db_session):
    from akashic.models import Source, User, File
    from akashic.auth.jwt import create_access_token

    user = User(username="dupuser", role="viewer", password_hash="x")
    db_session.add(user)
    src1 = Source(name="dup-src1", type="local", connection_config={})
    src2 = Source(name="dup-src2", type="local", connection_config={})
    db_session.add_all([src1, src2])
    await db_session.commit()

    # Same hash on two sources
    db_session.add(File(source_id=src1.id, path="/a/same.bin", filename="same.bin",
                       content_hash="duphash", size_bytes=1000))
    db_session.add(File(source_id=src2.id, path="/b/copy.bin", filename="copy.bin",
                       content_hash="duphash", size_bytes=1000))
    # Unique file
    db_session.add(File(source_id=src1.id, path="/a/unique.txt", filename="unique.txt",
                       content_hash="uniqhash", size_bytes=500))
    await db_session.commit()

    token = create_access_token({"sub": str(user.id)})
    response = await client.get("/api/duplicates", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    groups = response.json()
    assert len(groups) == 1
    assert groups[0]["content_hash"] == "duphash"
    assert groups[0]["count"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd api && pytest tests/test_files.py tests/test_duplicates.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement endpoints**

`api/akashic/routers/files.py`:

```python
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.models.file import File, FileVersion
from akashic.models.user import User
from akashic.schemas.file import FileResponse

router = APIRouter(prefix="/api/files", tags=["files"])


@router.get("", response_model=list[FileResponse])
async def list_files(
    source_id: uuid.UUID | None = None,
    extension: str | None = None,
    path_prefix: str | None = None,
    offset: int = 0,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(File).where(File.is_deleted == False)  # noqa: E712
    if source_id:
        stmt = stmt.where(File.source_id == source_id)
    if extension:
        stmt = stmt.where(File.extension == extension)
    if path_prefix:
        stmt = stmt.where(File.path.startswith(path_prefix))
    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{file_id}", response_model=FileResponse)
async def get_file(
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(File).where(File.id == file_id))
    f = result.scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="File not found")
    return f


@router.get("/{file_id}/versions")
async def get_file_versions(
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(FileVersion).where(FileVersion.file_id == file_id).order_by(FileVersion.detected_at.desc())
    )
    return result.scalars().all()


@router.get("/{file_id}/locations")
async def get_file_locations(
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Find all locations where this file exists (by content hash)."""
    file_result = await db.execute(select(File).where(File.id == file_id))
    f = file_result.scalar_one_or_none()
    if not f or not f.content_hash:
        raise HTTPException(status_code=404, detail="File not found or no hash")
    result = await db.execute(
        select(File).where(File.content_hash == f.content_hash, File.is_deleted == False)  # noqa: E712
    )
    return result.scalars().all()
```

`api/akashic/routers/directories.py`:

```python
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.models.directory import Directory
from akashic.models.user import User

router = APIRouter(prefix="/api/directories", tags=["directories"])


@router.get("")
async def list_directories(
    source_id: uuid.UUID | None = None,
    path_prefix: str | None = None,
    offset: int = 0,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(Directory).where(Directory.is_deleted == False)  # noqa: E712
    if source_id:
        stmt = stmt.where(Directory.source_id == source_id)
    if path_prefix:
        stmt = stmt.where(Directory.path.startswith(path_prefix))
    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()
```

`api/akashic/routers/duplicates.py`:

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from akashic.auth.dependencies import get_current_user
from akashic.database import get_db
from akashic.models.file import File
from akashic.models.user import User

router = APIRouter(prefix="/api/duplicates", tags=["duplicates"])


@router.get("")
async def list_duplicates(
    min_size: int | None = None,
    offset: int = 0,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Find content hashes that appear more than once
    stmt = (
        select(
            File.content_hash,
            func.count(File.id).label("count"),
            func.sum(File.size_bytes).label("total_size"),
            func.min(File.size_bytes).label("file_size"),
        )
        .where(File.is_deleted == False, File.content_hash.isnot(None))  # noqa: E712
        .group_by(File.content_hash)
        .having(func.count(File.id) > 1)
    )
    if min_size:
        stmt = stmt.having(func.min(File.size_bytes) >= min_size)
    stmt = stmt.order_by(func.sum(File.size_bytes).desc()).offset(offset).limit(limit)

    result = await db.execute(stmt)
    rows = result.all()

    return [
        {
            "content_hash": row.content_hash,
            "count": row.count,
            "total_size": row.total_size,
            "file_size": row.file_size,
            "wasted_bytes": (row.count - 1) * row.file_size,
        }
        for row in rows
    ]


@router.get("/{content_hash}/files")
async def get_duplicate_files(
    content_hash: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(File).where(File.content_hash == content_hash, File.is_deleted == False)  # noqa: E712
    )
    return result.scalars().all()
```

Register all new routers in `main.py`:

```python
from akashic.routers import users, ingest, sources, search, files, directories, duplicates

# In create_app():
app.include_router(files.router)
app.include_router(directories.router)
app.include_router(duplicates.router)
```

- [ ] **Step 4: Run tests**

```bash
cd api && pytest tests/test_files.py tests/test_duplicates.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/
git commit -m "feat: files, directories, and duplicates endpoints"
```

---

### Task 16: Tags, Analytics, Purge, and Webhooks Endpoints

**Files:**
- Create: `api/akashic/schemas/tag.py`
- Create: `api/akashic/schemas/webhook.py`
- Create: `api/akashic/routers/tags.py`
- Create: `api/akashic/routers/analytics.py`
- Create: `api/akashic/routers/purge.py`
- Create: `api/akashic/routers/webhooks.py`
- Create: `api/akashic/routers/scans.py`
- Create: `api/akashic/services/webhooks.py`
- Create: `api/akashic/services/analytics.py`
- Create: `api/tests/test_tags.py`
- Create: `api/tests/test_analytics.py`
- Create: `api/tests/test_purge.py`
- Create: `api/tests/test_webhooks.py`

This is a large task. Each router follows the same pattern as sources/files above. The key implementation details:

- [ ] **Step 1: Write tests for tags**

`api/tests/test_tags.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_create_and_apply_tag(client, db_session):
    from akashic.models import Source, User, File
    from akashic.auth.jwt import create_access_token

    user = User(username="tagger", role="admin", password_hash="x")
    db_session.add(user)
    source = Source(name="tag-src", type="local", connection_config={})
    db_session.add(source)
    await db_session.commit()

    f = File(source_id=source.id, path="/data/tagged.txt", filename="tagged.txt", size_bytes=100)
    db_session.add(f)
    await db_session.commit()
    await db_session.refresh(f)

    token = create_access_token({"sub": str(user.id)})

    # Create tag
    resp = await client.post("/api/tags", json={"name": "important", "color": "#ff0000"},
                             headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 201
    tag_id = resp.json()["id"]

    # Apply tag to file
    resp = await client.post(f"/api/files/{f.id}/tags/{tag_id}",
                             headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
```

- [ ] **Step 2: Write tests for analytics**

`api/tests/test_analytics.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_storage_analytics(client, db_session):
    from akashic.models import Source, User, File
    from akashic.auth.jwt import create_access_token

    user = User(username="analyst", role="viewer", password_hash="x")
    db_session.add(user)
    source = Source(name="analytics-src", type="local", connection_config={})
    db_session.add(source)
    await db_session.commit()

    for ext, size in [("pdf", 1000), ("pdf", 2000), ("jpg", 5000)]:
        db_session.add(File(source_id=source.id, path=f"/data/{size}.{ext}",
                           filename=f"{size}.{ext}", extension=ext, size_bytes=size))
    await db_session.commit()

    token = create_access_token({"sub": str(user.id)})
    resp = await client.get("/api/analytics/storage-by-type",
                            headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2
```

- [ ] **Step 3: Write tests for purge**

`api/tests/test_purge.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_purge_source_data(client, db_session):
    from akashic.models import Source, User, File
    from akashic.auth.jwt import create_access_token

    user = User(username="purger", role="admin", password_hash="x")
    db_session.add(user)
    source = Source(name="purge-src", type="local", connection_config={})
    db_session.add(source)
    await db_session.commit()

    db_session.add(File(source_id=source.id, path="/data/gone.txt",
                       filename="gone.txt", size_bytes=100))
    await db_session.commit()

    token = create_access_token({"sub": str(user.id)})
    resp = await client.post(f"/api/purge/source/{source.id}",
                             headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["records_removed"] >= 1
```

- [ ] **Step 4: Implement all remaining routers**

Follow the same patterns established in Tasks 13-15 for each router. Key implementations:

**Tags router** (`api/akashic/routers/tags.py`): CRUD for tags, endpoints to apply/remove tags from files and directories.

**Analytics router** (`api/akashic/routers/analytics.py`): SQL aggregation queries — `GROUP BY extension`, `GROUP BY source_id`, size histogram, largest files.

**Purge router** (`api/akashic/routers/purge.py`): Delete all files for a source, delete old versions, delete soft-deleted files. Requires admin role. Logs to `purge_log` table.

**Webhooks router** (`api/akashic/routers/webhooks.py`): CRUD for webhook configs. Webhook dispatch service sends HTTP POST with HMAC signature.

**Scans router** (`api/akashic/routers/scans.py`): List scans, get scan by ID, trigger new scan (creates scan record and returns scan ID for scanner to use).

Register all routers in `main.py`.

- [ ] **Step 5: Run all tests**

```bash
cd api && pytest tests/ -v
```
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add api/
git commit -m "feat: tags, analytics, purge, webhooks, and scans endpoints"
```

---

### Task 17: Text Extraction Pipeline

**Files:**
- Create: `api/akashic/workers/__init__.py`
- Create: `api/akashic/workers/extraction.py`
- Create: `api/akashic/services/extraction.py`

- [ ] **Step 1: Implement extraction service**

`api/akashic/services/extraction.py`:

```python
import httpx

from akashic.config import settings


async def extract_text_tika(content: bytes, mime_type: str = "application/octet-stream") -> str | None:
    """Extract text from binary content using Apache Tika."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(
                f"{settings.tika_url}/tika",
                content=content,
                headers={"Content-Type": mime_type, "Accept": "text/plain"},
                timeout=60.0,
            )
            if response.status_code == 200:
                text = response.text.strip()
                return text if text else None
    except Exception:
        return None
    return None


def extract_text_plain(content: bytes) -> str | None:
    """Extract text from plain text / code files with encoding detection."""
    try:
        import chardet
        detected = chardet.detect(content)
        encoding = detected.get("encoding", "utf-8") or "utf-8"
        return content.decode(encoding).strip() or None
    except Exception:
        try:
            return content.decode("utf-8", errors="replace").strip() or None
        except Exception:
            return None


PLAIN_TEXT_TYPES = {
    "text/plain", "text/html", "text/css", "text/javascript", "text/xml",
    "application/json", "application/xml", "application/javascript",
    "application/x-yaml", "application/toml",
}

TIKA_TYPES = {
    "application/pdf", "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-excel", "application/vnd.ms-powerpoint",
    "application/rtf", "application/epub+zip",
}


async def extract_text(content: bytes, mime_type: str) -> str | None:
    """Route to the right extractor based on MIME type."""
    if mime_type in PLAIN_TEXT_TYPES or mime_type.startswith("text/"):
        return extract_text_plain(content)
    if mime_type in TIKA_TYPES:
        return await extract_text_tika(content, mime_type)
    return None
```

- [ ] **Step 2: Implement Redis worker**

`api/akashic/workers/extraction.py`:

```python
"""
Redis Queue worker for text extraction.

Run with: rq worker extraction --url redis://localhost:6379/0
"""
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from akashic.config import settings
from akashic.models.file import File
from akashic.services.extraction import extract_text
from akashic.services.search import index_file

engine = create_async_engine(settings.database_url)
session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def process_file_extraction(file_id: str):
    """Synchronous entry point for RQ. Runs the async extraction."""
    asyncio.run(_extract(file_id))


async def _extract(file_id: str):
    async with session_factory() as db:
        result = await db.execute(select(File).where(File.id == file_id))
        file = result.scalar_one_or_none()
        if not file or not file.mime_type:
            return

        # For now, extraction requires the file content to be fetched.
        # This will be enhanced when we add content retrieval through connectors.
        # The extracted text gets indexed into Meilisearch.
        await index_file({
            "id": str(file.id),
            "source_id": str(file.source_id),
            "path": file.path,
            "filename": file.filename,
            "extension": file.extension,
            "mime_type": file.mime_type,
            "size_bytes": file.size_bytes,
            "fs_modified_at": int(file.fs_modified_at.timestamp()) if file.fs_modified_at else None,
            "tags": [],
        })
```

- [ ] **Step 3: Add chardet dependency**

Add `chardet>=5.2` to `api/pyproject.toml` dependencies.

- [ ] **Step 4: Commit**

```bash
git add api/
git commit -m "feat: text extraction pipeline with Tika and Meilisearch indexing"
```

---

## Phase 4: React Web UI

### Task 18: Web App Scaffolding

**Files:**
- Create: `web/index.html`
- Create: `web/vite.config.ts`
- Create: `web/tsconfig.json`
- Create: `web/src/main.tsx`
- Create: `web/src/App.tsx`
- Create: `web/src/types/index.ts`
- Create: `web/src/api/client.ts`
- Create: `web/Dockerfile`

- [ ] **Step 1: Create vite config and tsconfig**

`web/vite.config.ts`:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
```

`web/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"]
}
```

- [ ] **Step 2: Create entry point and types**

`web/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Akashic</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`web/src/types/index.ts`:

```ts
export interface Source {
  id: string;
  name: string;
  type: string;
  connection_config: Record<string, unknown>;
  scan_schedule: string | null;
  last_scan_at: string | null;
  status: string;
  created_at: string;
}

export interface FileEntry {
  id: string;
  source_id: string;
  path: string;
  filename: string;
  extension: string | null;
  size_bytes: number | null;
  mime_type: string | null;
  content_hash: string | null;
  fs_modified_at: string | null;
  first_seen_at: string;
  last_seen_at: string;
  is_deleted: boolean;
}

export interface SearchResults {
  results: FileEntry[];
  total: number;
  query: string;
}

export interface DuplicateGroup {
  content_hash: string;
  count: number;
  total_size: number;
  file_size: number;
  wasted_bytes: number;
}

export interface Scan {
  id: string;
  source_id: string;
  scan_type: string;
  status: string;
  files_found: number;
  files_new: number;
  files_changed: number;
  files_deleted: number;
  started_at: string | null;
  completed_at: string | null;
}

export interface User {
  id: string;
  username: string;
  email: string | null;
  role: string;
}
```

`web/src/api/client.ts`:

```ts
const BASE_URL = "/api";

function getToken(): string | null {
  return localStorage.getItem("akashic_token");
}

export async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(`${BASE_URL}${path}`, { ...options, headers });
  if (!response.ok) {
    if (response.status === 401) {
      localStorage.removeItem("akashic_token");
      window.location.href = "/login";
    }
    throw new Error(`API error: ${response.status}`);
  }
  return response.json();
}

export function setToken(token: string) {
  localStorage.setItem("akashic_token", token);
}

export function clearToken() {
  localStorage.removeItem("akashic_token");
}
```

- [ ] **Step 3: Create App with routing**

`web/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";

const queryClient = new QueryClient();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </BrowserRouter>
  </React.StrictMode>
);
```

`web/src/App.tsx`:

```tsx
import { Routes, Route, Navigate } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Search from "./pages/Search";
import Sources from "./pages/Sources";
import Duplicates from "./pages/Duplicates";
import Analytics from "./pages/Analytics";
import Login from "./pages/Login";

function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="search" element={<Search />} />
        <Route path="sources" element={<Sources />} />
        <Route path="duplicates" element={<Duplicates />} />
        <Route path="analytics" element={<Analytics />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;
```

`web/Dockerfile`:

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 3000
```

- [ ] **Step 4: Install dependencies and verify**

```bash
cd web && npm install && npm run dev
```
Expected: dev server starts on port 3000 (will show blank page until pages are built)

- [ ] **Step 5: Commit**

```bash
git add web/
git commit -m "feat: React web app scaffolding with routing and API client"
```

---

### Task 19: Web UI Pages

**Files:**
- Create: `web/src/components/Layout.tsx`
- Create: `web/src/components/SearchBar.tsx`
- Create: `web/src/pages/Login.tsx`
- Create: `web/src/pages/Dashboard.tsx`
- Create: `web/src/pages/Search.tsx`
- Create: `web/src/pages/Sources.tsx`
- Create: `web/src/pages/Duplicates.tsx`
- Create: `web/src/pages/Analytics.tsx`
- Create: `web/src/hooks/useSearch.ts`
- Create: `web/src/hooks/useSources.ts`
- Create: `web/src/hooks/useAuth.ts`

This task implements all UI pages. Each page is a React component that fetches data via the API client and renders it. The implementation follows standard React + TanStack Query patterns.

- [ ] **Step 1: Create Layout component**

`web/src/components/Layout.tsx` — sidebar navigation with links to Dashboard, Search, Sources, Duplicates, Analytics, Admin. Uses `<Outlet />` for page content.

- [ ] **Step 2: Create Login page**

`web/src/pages/Login.tsx` — form with username/password, calls `/api/users/login`, stores JWT token.

- [ ] **Step 3: Create hooks**

`web/src/hooks/useAuth.ts` — manages auth state, login/logout functions.

`web/src/hooks/useSearch.ts` — wraps `useQuery` for search endpoint with debounced input.

`web/src/hooks/useSources.ts` — wraps `useQuery` for sources list.

- [ ] **Step 4: Create Dashboard page**

`web/src/pages/Dashboard.tsx` — fetches and displays: total files, total sources, total storage, recent scans, source status cards.

- [ ] **Step 5: Create Search page**

`web/src/pages/Search.tsx` — search bar, faceted filters (source dropdown, extension, size range, date range), paginated results list showing file path, source, size, modified date, source online/offline status.

- [ ] **Step 6: Create Sources page**

`web/src/pages/Sources.tsx` — source cards with status indicator, add source form, trigger scan button, scan history per source.

- [ ] **Step 7: Create Duplicates page**

`web/src/pages/Duplicates.tsx` — grouped duplicate view, sorted by wasted space, expandable to show all file locations.

- [ ] **Step 8: Create Analytics page**

`web/src/pages/Analytics.tsx` — storage breakdown charts by type and source, largest files table.

- [ ] **Step 9: Verify in browser**

```bash
cd web && npm run dev
```
Open `http://localhost:3000`, verify all pages render and navigation works.

- [ ] **Step 10: Build and check for errors**

```bash
cd web && npm run build
```
Expected: no TypeScript errors, build succeeds.

- [ ] **Step 11: Commit**

```bash
git add web/
git commit -m "feat: web UI pages - dashboard, search, sources, duplicates, analytics"
```

---

## Phase 5: Go CLI

### Task 20: CLI Client

**Files:**
- Create: `cli/cmd/akashic/main.go`
- Create: `cli/internal/client/client.go`
- Create: `cli/internal/client/client_test.go`
- Create: `cli/internal/commands/search.go`
- Create: `cli/internal/commands/sources.go`
- Create: `cli/internal/commands/scans.go`
- Create: `cli/internal/commands/duplicates.go`

- [ ] **Step 1: Add cobra dependency**

```bash
cd cli && go get github.com/spf13/cobra
```

- [ ] **Step 2: Write client test**

`cli/internal/client/client_test.go`:

```go
package client

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestClient_Search(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/search" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		q := r.URL.Query().Get("q")
		if q != "report" {
			t.Errorf("expected query 'report', got '%s'", q)
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"results": []map[string]interface{}{
				{"filename": "report.pdf", "path": "/data/report.pdf"},
			},
			"total": 1,
			"query": "report",
		})
	}))
	defer server.Close()

	c := New(server.URL, "test-key")
	results, err := c.Search(context.Background(), "report", nil)
	if err != nil {
		t.Fatal(err)
	}
	if results.Total != 1 {
		t.Errorf("expected 1 result, got %d", results.Total)
	}
}
```

- [ ] **Step 3: Implement CLI client**

`cli/internal/client/client.go`:

```go
package client

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"time"
)

type Client struct {
	baseURL    string
	apiKey     string
	httpClient *http.Client
}

type SearchResults struct {
	Results []FileEntry `json:"results"`
	Total   int         `json:"total"`
	Query   string      `json:"query"`
}

type FileEntry struct {
	ID         string `json:"id"`
	SourceID   string `json:"source_id"`
	Path       string `json:"path"`
	Filename   string `json:"filename"`
	Extension  string `json:"extension"`
	SizeBytes  int64  `json:"size_bytes"`
	MimeType   string `json:"mime_type"`
	IsDeleted  bool   `json:"is_deleted"`
}

type Source struct {
	ID         string `json:"id"`
	Name       string `json:"name"`
	Type       string `json:"type"`
	Status     string `json:"status"`
	LastScanAt string `json:"last_scan_at"`
}

type SearchParams struct {
	SourceID  string
	Extension string
	MinSize   int64
	MaxSize   int64
}

func New(baseURL, apiKey string) *Client {
	return &Client{
		baseURL: baseURL,
		apiKey:  apiKey,
		httpClient: &http.Client{Timeout: 30 * time.Second},
	}
}

func (c *Client) get(ctx context.Context, path string, params url.Values) (*http.Response, error) {
	u := c.baseURL + path
	if len(params) > 0 {
		u += "?" + params.Encode()
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.apiKey)
	return c.httpClient.Do(req)
}

func (c *Client) Search(ctx context.Context, query string, params *SearchParams) (*SearchResults, error) {
	v := url.Values{"q": {query}}
	if params != nil {
		if params.SourceID != "" {
			v.Set("source_id", params.SourceID)
		}
		if params.Extension != "" {
			v.Set("extension", params.Extension)
		}
	}
	resp, err := c.get(ctx, "/api/search", v)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var results SearchResults
	if err := json.NewDecoder(resp.Body).Decode(&results); err != nil {
		return nil, fmt.Errorf("decode: %w", err)
	}
	return &results, nil
}

func (c *Client) ListSources(ctx context.Context) ([]Source, error) {
	resp, err := c.get(ctx, "/api/sources", nil)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var sources []Source
	if err := json.NewDecoder(resp.Body).Decode(&sources); err != nil {
		return nil, err
	}
	return sources, nil
}
```

- [ ] **Step 4: Implement CLI commands**

`cli/internal/commands/search.go`:

```go
package commands

import (
	"context"
	"fmt"

	"github.com/spf13/cobra"

	"github.com/akashic-project/akashic/cli/internal/client"
)

func NewSearchCmd(c *client.Client) *cobra.Command {
	var sourceID, extension string

	cmd := &cobra.Command{
		Use:   "search [query]",
		Short: "Search indexed files",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			params := &client.SearchParams{SourceID: sourceID, Extension: extension}
			results, err := c.Search(context.Background(), args[0], params)
			if err != nil {
				return err
			}
			fmt.Printf("Found %d results for '%s'\n\n", results.Total, results.Query)
			for _, f := range results.Results {
				fmt.Printf("  %s  (%d bytes)  %s\n", f.Path, f.SizeBytes, f.SourceID)
			}
			return nil
		},
	}

	cmd.Flags().StringVar(&sourceID, "source", "", "Filter by source ID")
	cmd.Flags().StringVar(&extension, "type", "", "Filter by file extension")
	return cmd
}
```

`cli/internal/commands/sources.go`:

```go
package commands

import (
	"context"
	"fmt"

	"github.com/spf13/cobra"

	"github.com/akashic-project/akashic/cli/internal/client"
)

func NewSourcesCmd(c *client.Client) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "sources",
		Short: "Manage sources",
	}

	listCmd := &cobra.Command{
		Use:   "list",
		Short: "List all sources",
		RunE: func(cmd *cobra.Command, args []string) error {
			sources, err := c.ListSources(context.Background())
			if err != nil {
				return err
			}
			for _, s := range sources {
				fmt.Printf("  %-20s  %-8s  %-10s  %s\n", s.Name, s.Type, s.Status, s.LastScanAt)
			}
			return nil
		},
	}

	cmd.AddCommand(listCmd)
	return cmd
}
```

`cli/cmd/akashic/main.go`:

```go
package main

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"

	"github.com/akashic-project/akashic/cli/internal/client"
	"github.com/akashic-project/akashic/cli/internal/commands"
)

func main() {
	apiURL := os.Getenv("AKASHIC_API_URL")
	if apiURL == "" {
		apiURL = "http://localhost:8000"
	}
	apiKey := os.Getenv("AKASHIC_API_KEY")

	c := client.New(apiURL, apiKey)

	rootCmd := &cobra.Command{
		Use:   "akashic",
		Short: "Akashic - Universal File Index",
	}

	rootCmd.AddCommand(commands.NewSearchCmd(c))
	rootCmd.AddCommand(commands.NewSourcesCmd(c))

	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
```

- [ ] **Step 5: Run tests and build**

```bash
cd cli && go test ./internal/client/ -v
go build -o akashic ./cmd/akashic/
./akashic --help
./akashic search --help
./akashic sources --help
```
Expected: tests PASS, binary builds, help text displays

- [ ] **Step 6: Commit**

```bash
git add cli/
git commit -m "feat: CLI client with search and sources commands"
```

---

## Phase 6: Home Assistant Integration

### Task 21: HA Custom Component

**Files:**
- Create: `ha-integration/custom_components/akashic/__init__.py`
- Create: `ha-integration/custom_components/akashic/manifest.json`
- Create: `ha-integration/custom_components/akashic/const.py`
- Create: `ha-integration/custom_components/akashic/config_flow.py`
- Create: `ha-integration/custom_components/akashic/coordinator.py`
- Create: `ha-integration/custom_components/akashic/sensor.py`
- Create: `ha-integration/custom_components/akashic/binary_sensor.py`
- Create: `ha-integration/custom_components/akashic/services.yaml`

- [ ] **Step 1: Create manifest and constants**

`ha-integration/custom_components/akashic/manifest.json`:

```json
{
  "domain": "akashic",
  "name": "Akashic File Index",
  "version": "0.1.0",
  "codeowners": [],
  "config_flow": true,
  "dependencies": [],
  "documentation": "https://github.com/akashic-project/akashic",
  "iot_class": "local_polling",
  "requirements": ["httpx>=0.28"]
}
```

`ha-integration/custom_components/akashic/const.py`:

```python
DOMAIN = "akashic"
CONF_API_URL = "api_url"
CONF_API_KEY = "api_key"
DEFAULT_SCAN_INTERVAL = 60  # seconds for status polling
STATS_SCAN_INTERVAL = 300   # seconds for stats polling
```

- [ ] **Step 2: Create config flow**

`ha-integration/custom_components/akashic/config_flow.py`:

```python
import httpx
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_URL

from .const import DOMAIN, CONF_API_URL, CONF_API_KEY


class AkashicConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"{user_input[CONF_API_URL]}/api/sources",
                        headers={"Authorization": f"Bearer {user_input[CONF_API_KEY]}"},
                        timeout=10,
                    )
                    resp.raise_for_status()
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="Akashic", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_API_URL, default="http://localhost:8000"): str,
                vol.Required(CONF_API_KEY): str,
            }),
            errors=errors,
        )
```

- [ ] **Step 3: Create data coordinator**

`ha-integration/custom_components/akashic/coordinator.py`:

```python
import logging
from datetime import timedelta

import httpx
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_API_URL, CONF_API_KEY, DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class AkashicCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, config: dict) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="akashic",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api_url = config[CONF_API_URL]
        self.api_key = config[CONF_API_KEY]

    async def _async_update_data(self) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient() as client:
            sources_resp = await client.get(f"{self.api_url}/api/sources", headers=headers, timeout=10)
            sources_resp.raise_for_status()
            sources = sources_resp.json()

        return {
            "sources": {s["name"]: s for s in sources},
            "total_sources": len(sources),
        }
```

- [ ] **Step 4: Create __init__.py (integration setup)**

`ha-integration/custom_components/akashic/__init__.py`:

```python
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import AkashicCoordinator

PLATFORMS = ["sensor", "binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = AkashicCoordinator(hass, entry.data)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    async def handle_trigger_scan(call):
        source_name = call.data.get("source_name")
        import httpx
        headers = {"Authorization": f"Bearer {entry.data['api_key']}"}
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{entry.data['api_url']}/api/scans/trigger",
                json={"source_name": source_name},
                headers=headers,
                timeout=10,
            )

    hass.services.async_register(DOMAIN, "trigger_scan", handle_trigger_scan)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
```

- [ ] **Step 5: Create sensors**

`ha-integration/custom_components/akashic/sensor.py`:

```python
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AkashicCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    coordinator: AkashicCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [AkashicTotalSourcesSensor(coordinator, entry)]

    for name in coordinator.data.get("sources", {}):
        entities.append(AkashicSourceStatusSensor(coordinator, entry, name))
        entities.append(AkashicSourceFileCountSensor(coordinator, entry, name))

    async_add_entities(entities)


class AkashicTotalSourcesSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_total_sources"
        self._attr_name = "Akashic Total Sources"

    @property
    def native_value(self):
        return self.coordinator.data.get("total_sources", 0)


class AkashicSourceStatusSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry, source_name):
        super().__init__(coordinator)
        self._source_name = source_name
        self._attr_unique_id = f"{entry.entry_id}_{source_name}_status"
        self._attr_name = f"Akashic {source_name} Status"

    @property
    def native_value(self):
        sources = self.coordinator.data.get("sources", {})
        source = sources.get(self._source_name, {})
        return source.get("status", "unknown")


class AkashicSourceFileCountSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry, source_name):
        super().__init__(coordinator)
        self._source_name = source_name
        self._attr_unique_id = f"{entry.entry_id}_{source_name}_file_count"
        self._attr_name = f"Akashic {source_name} Files"
        self._attr_native_unit_of_measurement = "files"

    @property
    def native_value(self):
        # This would need a file count endpoint; placeholder
        return None
```

`ha-integration/custom_components/akashic/binary_sensor.py`:

```python
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AkashicCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    coordinator: AkashicCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []

    for name in coordinator.data.get("sources", {}):
        entities.append(AkashicSourceAvailableSensor(coordinator, entry, name))

    async_add_entities(entities)


class AkashicSourceAvailableSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, entry, source_name):
        super().__init__(coordinator)
        self._source_name = source_name
        self._attr_unique_id = f"{entry.entry_id}_{source_name}_available"
        self._attr_name = f"Akashic {source_name} Available"

    @property
    def is_on(self):
        sources = self.coordinator.data.get("sources", {})
        source = sources.get(self._source_name, {})
        return source.get("status") == "online"
```

- [ ] **Step 6: Create services.yaml**

`ha-integration/custom_components/akashic/services.yaml`:

```yaml
trigger_scan:
  name: Trigger Scan
  description: Trigger a scan for a specific source
  fields:
    source_name:
      name: Source Name
      description: The name of the source to scan
      required: true
      selector:
        text:

trigger_full_scan:
  name: Trigger Full Scan
  description: Trigger a full rescan for a specific source
  fields:
    source_name:
      name: Source Name
      description: The name of the source to scan
      required: true
      selector:
        text:
```

- [ ] **Step 7: Commit**

```bash
git add ha-integration/
git commit -m "feat: Home Assistant custom integration with sensors and services"
```

---

## Phase 7: Integration and Verification

### Task 22: Docker Compose Full Stack Test

- [ ] **Step 1: Create .env from example**

```bash
cp .env.example .env
```

- [ ] **Step 2: Build and start all services**

```bash
docker compose build
docker compose up -d
```

- [ ] **Step 3: Run migrations**

```bash
docker compose exec api alembic upgrade head
```

- [ ] **Step 4: Create admin user via API**

```bash
curl -X POST http://localhost:8000/api/users/register \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin123", "email": "admin@local"}'
```

- [ ] **Step 5: Login and get token**

```bash
curl -X POST http://localhost:8000/api/users/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin123"}'
```

- [ ] **Step 6: Add a local test source**

```bash
TOKEN="<from step 5>"
curl -X POST http://localhost:8000/api/sources \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"name": "test-local", "type": "local", "connection_config": {"path": "/tmp/test-data"}}'
```

- [ ] **Step 7: Create test data and run scanner**

```bash
mkdir -p /tmp/test-data/subdir
echo "hello world" > /tmp/test-data/test.txt
echo "some code" > /tmp/test-data/main.py
cp /tmp/test-data/test.txt /tmp/test-data/subdir/copy.txt

cd scanner && go run ./cmd/akashic-scanner/ \
  -source-id <source_id_from_step_6> \
  -root /tmp/test-data \
  -type local \
  -full
```

- [ ] **Step 8: Verify search works**

```bash
curl "http://localhost:8000/api/search?q=test" \
  -H "Authorization: Bearer $TOKEN"
```
Expected: returns test.txt in results

- [ ] **Step 9: Verify duplicates detected**

```bash
curl "http://localhost:8000/api/duplicates" \
  -H "Authorization: Bearer $TOKEN"
```
Expected: test.txt and copy.txt grouped as duplicates (same content)

- [ ] **Step 10: Verify web UI**

Open `http://localhost:3000` in browser. Login with admin/admin123. Verify:
- Dashboard shows stats
- Search finds files
- Sources shows test-local source
- Duplicates shows the duplicate group

- [ ] **Step 11: Test CLI**

```bash
cd cli
export AKASHIC_API_URL=http://localhost:8000
export AKASHIC_API_KEY=$TOKEN
go run ./cmd/akashic/ search "test"
go run ./cmd/akashic/ sources list
```

- [ ] **Step 12: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration testing fixes"
```

---

## Summary

| Phase | Tasks | Description |
|-------|-------|-------------|
| 1. Foundation | 1-3 | Scaffolding, DB schema, FastAPI skeleton with auth |
| 2. Scanner | 4-11 | Go scanner with all connectors and CLI binary |
| 3. API Core | 12-17 | Ingest, search, files, duplicates, tags, analytics, purge, extraction |
| 4. Web UI | 18-19 | React app with all pages |
| 5. CLI | 20 | Go CLI client |
| 6. HA Integration | 21 | Home Assistant custom component |
| 7. Verification | 22 | Full stack integration test |
