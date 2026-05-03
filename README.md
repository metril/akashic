# Akashic

Universal file index — hybrid Go scanner + Python API + Meilisearch + React web UI.

## Stack

- **Scanner** (Go) — walks filesystems, hashes content, posts batches to the API.
- **API** (FastAPI / Python 3.12) — ingest, search, RBAC, scheduling, webhooks.
- **Web** (React + Vite + Tailwind) — admin dashboard. The storage view
  uses Canvas2D (sunburst) and WebGL2 (treemap) for 60 fps at 50 k+
  rectangles; see [docs/storage-view.md](docs/storage-view.md) for the
  fallback behaviour on browsers without WebGL2.
- **Storage** — PostgreSQL, Meilisearch, Redis (extraction queue), Tika (text extraction).
- **CLI** (Go) — `akashic` binary with `search / sources / scan / duplicates / tags / purge` subcommands; meaningful exit codes (1 user-error, 2 server-error, 3 scan-failed) for scripting.

## Documentation

- **[Configuration reference](docs/configuration.md)** — every env var and feature flag.
- **[Authentication](docs/authentication.md)** — local users, OIDC, direct LDAP, OIDC + LDAP fallback.
- **[Permissions model](docs/permissions-model.md)** — ACL projection, `viewable_by_*` columns, FsBindings.
- **[Storage view](docs/storage-view.md)** — treemap, sunburst, colour modes (incl. Risk).
- **[Search and Browse](docs/search-and-browse.md)** — query syntax, filter chips, search-as.
- **[Tags](docs/tags.md)** — tag model, inheritance, bulk-apply.
- **[Duplicates](docs/duplicates.md)** — dedupe detection + bulk-delete.
- **[Webhooks](docs/webhooks.md)** — scan lifecycle events, signature verification.
- **[Admin tools](docs/admin.md)** — audit log, effective-permissions evaluator.
- **[Migrations](docs/migrations.md)** — Alembic workflow.
- **[CHANGELOG](CHANGELOG.md)** — release notes per version.

## Install — pre-built images

Tagged releases publish multi-arch (`linux/amd64`, `linux/arm64`) images to
[GitHub Container Registry](https://github.com/metril/akashic/pkgs/container/akashic-api)
and statically-linked scanner binaries to the
[Releases page](https://github.com/metril/akashic/releases).

```sh
# Pull the latest stable release of both images:
docker pull ghcr.io/metril/akashic-api:latest
docker pull ghcr.io/metril/akashic-web:latest

# Pin a specific version:
docker pull ghcr.io/metril/akashic-api:v0.1.0
docker pull ghcr.io/metril/akashic-web:v0.1.0
```

Use the bundled `compose.release.yaml` to bring up the full stack against
those pre-built images:

```sh
# Latest stable (default):
docker compose -f compose.release.yaml up -d

# Pinned release:
AKASHIC_VERSION=v0.1.0 docker compose -f compose.release.yaml up -d
```

## Run from source

If you want to build locally instead, the bundled `compose.yaml` will build
both images from this checkout:

```sh
make up         # docker compose up -d
make ps         # docker compose ps
make logs       # tail api + web logs
make down       # stop all services
```

The web dashboard is exposed on `http://<host>:3000`. The API is bound to localhost
on port 8000. All other services bind to localhost only.

## Develop

After editing source code, the running containers do **not** auto-pick up changes —
the API and web images embed their code at build time.

Two ways to keep them in sync:

```sh
# One-shot rebuild + restart of just the changed service:
make web        # rebuild + restart web container
make api        # rebuild + restart api container

# Continuous: any save to web/src or api/akashic triggers a rebuild
make watch      # docker compose watch
```

For the fastest frontend loop, skip Docker entirely and run Vite directly:

```sh
cd web && npm install && npm run dev
```

Vite serves on `http://localhost:3000` and proxies `/api/*` to the API container
at `localhost:8000`.

## Configuration

The API reads `.env` from the repo root (see
[compose.yaml](compose.yaml) `api.env_file`). The full env-var
reference lives in [docs/configuration.md](docs/configuration.md);
the most commonly-overridden keys are:

| Key | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://…` | Postgres DSN |
| `MEILI_URL` / `MEILI_KEY` | local | Meilisearch endpoint |
| `REDIS_URL` | local | Extraction queue backend |
| `SECRET_KEY` | `changeme-secret-key` | JWT signing key — **override in production** |
| `OIDC_ENABLED` | `false` | Enable SSO. See [docs/authentication.md](docs/authentication.md). |
| `LDAP_ENABLED` | `false` | Enable direct LDAP login. See [docs/authentication.md](docs/authentication.md). |
| `BROWSE_ENFORCE_PERMS` | `false` | Apply per-user ACL filter to Browse |
| `STREAMING_TOPCHILDREN` | `false` | Live mid-scan updates in the Storage view |
| `STALE_SCAN_THRESHOLD_MINUTES` | `60` | Watchdog window for pending/running scans |

## First-time login

The first user to register at `POST /api/users/register` becomes
admin; registration closes after that. For SSO setup (OIDC,
direct LDAP, or OIDC + LDAP fallback), see
[docs/authentication.md](docs/authentication.md).

## Scanners

Scans run on **scanner agents**, not on the api host. The api enqueues
a pending scan; an agent registered with that source's pool picks it
up via [`POST /api/scans/lease`](api/akashic/routers/scanners.py).
Agents can run anywhere with HTTP reach to the api, so:

- **Multi-site:** put one agent in each site's network (e.g. `pool=hq`,
  `pool=site-amsterdam`) and tag each source with the matching pool.
  The agent only needs to see its local data; the api never touches
  the share directly.
- **Parallel throughput:** put N agents in the same pool. They race
  for jobs via Postgres `FOR UPDATE SKIP LOCKED`, so each pending
  scan is claimed exactly once.
- **Permissive routing:** sources with no `preferred_pool` can be
  claimed by any registered agent; sources with a pool tag are
  pinned to that pool.

### Provisioning a scanner

Three onboarding paths, in order of recommendation. The first two
generate the keypair on the scanner host so the private key never
crosses the wire.

#### 1. Join token (recommended)

Admin mints a one-time token in the UI; the scanner self-registers.

```sh
# 1. Settings → Scanners → "Generate token".
#    Paste the resulting command into your scanner host:
docker run --rm \
  -v akashic-scanner-data:/secrets \
  ghcr.io/metril/akashic-scanner:latest \
  claim --api=https://akashic.example.com --token=akcl_… --start-after

# The wizard's "Step 3 / live confirmation" page flips to a green
# success card the moment the scanner registers (~1 s).
```

The token can also be tightened with optional restrictions —
"this scanner is only allowed to claim work for source X" or "only
incremental scans". Useful for low-power Pi scanners or NAS-pinned
agents.

#### 2. Discovery (operator approves)

Turn on `discovery_enabled` in Settings → Scanners. A scanner
without a token POSTs its public key + a pairing code:

```sh
docker run --rm \
  -v akashic-scanner-data:/secrets \
  ghcr.io/metril/akashic-scanner:latest \
  discover --api=https://akashic.example.com --start-after
# stderr shows:  Pairing code: ABCD-EFGH
#                Approve in the Akashic UI: …/settings/scanners#pending
```

The "Pending claims" pane in the UI shows a row with that pairing
code. Confirm the code matches, click Approve (with a name + pool),
and the scanner unblocks.

#### 3. Manual key (legacy / scripted)

`POST /api/scanners` returns the keypair the api generated for you.
The private key is shown **once** in the modal and downloadable as
a `.pem`. Use this when scripting bootstrap into an existing
secrets store.

```sh
docker run -d --restart=unless-stopped \
  -v /etc/akashic:/secrets:ro \
  ghcr.io/metril/akashic-scanner:latest \
  agent \
    --api=https://akashic.example.com \
    --scanner-id=<uuid-from-modal> \
    --key=/secrets/scanner.key
```

For local-dev single-host installs, `make bootstrap-scanner`
automates path 1 against the running api: it mints a join token,
runs `akashic-scanner claim` inside the bundled scanner container,
and stamps `SCANNER_ID` into `.env` so `make scanner` brings up
the agent service.

### Authentication model

Every agent → api request carries a `Bearer` JWT signed with the
scanner's private key (EdDSA / Ed25519). The api verifies the
signature against the registered public key and rejects on
fingerprint mismatch, expiry (5-minute window, ±30 s clock skew),
or wrong issuer. Compromise of one private key is bounded to that
one scanner; rotate it from the UI's "Rotate keys" button — the old
key stops authenticating immediately.

## Releases

CI runs on every push to `main` (build + test) and on every `v*.*.*` tag
(build + test + publish). The full pipeline lives under
[.github/workflows](.github/workflows).

Cutting a release:

```sh
# 1. Tag the commit you want to ship (semver; pre-releases use a hyphen, e.g. v0.1.0-rc.1):
git tag -a v0.1.0 -m "v0.1.0"
git push github v0.1.0

# 2. The release workflow runs the test matrix, then:
#    - Publishes ghcr.io/metril/akashic-api:v0.1.0  (and :latest for stable)
#    - Publishes ghcr.io/metril/akashic-web:v0.1.0  (and :latest for stable)
#    - Cross-compiles akashic-scanner for linux-amd64, linux-arm64, darwin-arm64
#    - Creates a GitHub Release with auto-generated notes + scanner tarballs
```

`-rc`, `-beta`, etc. (anything with a hyphen) are flagged as pre-releases
and skip the `:latest` Docker tag, so consumers stay on the last stable.
