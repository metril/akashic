# Configuration

`docker compose up` works with **no `.env` file**. Almost everything
is configured in the web UI at **Settings → Server** (admin only) and
stored in the database; changes apply without a restart.

## Bootstrap variables (API)

Only these are read from the environment. The shipped compose files
set them for you.

| Var | Default | What |
|-----|---------|------|
| `DATABASE_URL` | compose-generated | Postgres DSN (asyncpg dialect). |
| `REDIS_URL` | `redis://redis:6379/0` | Redis pubsub + dirty-set storage. |
| `MEILI_URL` | `http://meilisearch:7700` | Meilisearch base URL. |
| `MEILI_KEY` | compose-generated | Meilisearch master key. |
| `SECRET_KEY_FILE` | `/data/secret.key` | JWT signing key file. Auto-generated (0600) on first boot; keep the `/data` volume, and share it across all API replicas (see below). |

`APP_VERSION`, `AKASHIC_SCANNER_BIN` and `FORWARDED_ALLOW_IPS` are
image-internal defaults. Behind a reverse proxy, set
`FORWARDED_ALLOW_IPS` to its address so login rate limits see the real
client IP.

### Generated secrets

An `init` service writes random `postgres_password` and `meili_key`
files into the `secrets` volume on first run (idempotent), and the
`api` service builds `DATABASE_URL`/`MEILI_KEY` from them.

## Settings → Server (UI)

| Group | Covers |
|-------|--------|
| Authentication | OIDC (discovery URL, client, claims, strategy), LDAP, local login, token TTLs. See [authentication.md](authentication.md). |
| Network | CORS origins, frontend URL, redirect URI. |
| Permissions | Group cache TTL, Browse ACL enforcement. See [permissions-model.md](permissions-model.md). |
| Audit | Retention days. See [admin.md](admin.md). |
| Performance | Streaming top-children, slow-query thresholds. See [storage-view.md](storage-view.md). |
| Scanning | Stale-scan watchdog, scanner discovery, Tika URL. |

Per-scanner settings (Tika URL, extraction workers, concurrent units)
are set per scanner in **Settings → Scanners**; the scanner only needs
`AKASHIC_API_URL`, `AKASHIC_DISCOVER`/`AKASHIC_CLAIM_TOKEN`,
`SCANNER_ID` and its key/id paths.

## Upgrading from an env-based install

- **Legacy env values** (OIDC, LDAP, CORS, TTLs, flags, `SECRET_KEY`)
  are imported once into the database on first boot, then ignored (a
  warning is logged). `SECRET_KEY` is persisted to `SECRET_KEY_FILE`.
  The API container reads `.env` (optional) only for this one-time
  import; once the first boot has finished you can delete `.env`.
- **Existing Postgres volume**: the generated password will not match
  it. Run once with your old values so `init` writes them instead:

  ```sh
  POSTGRES_PASSWORD=<old> MEILI_MASTER_KEY=<old> docker compose up -d
  ```

  (or keep them in `.env`; compose still reads it for these
  substitutions). Non-default `POSTGRES_USER`/`POSTGRES_DB` are no
  longer supported by the compose files; they are fixed to `akashic`.

## Multiple API replicas

The signing/encryption key lives in `SECRET_KEY_FILE` on the `/data`
volume (`apidata:/data` in the compose files). Every API replica must
mount the **same** volume; otherwise each replica generates its own key
and tokens or encrypted secrets from one replica fail on another. Back
the volume up: losing it makes stored secrets unreadable.
