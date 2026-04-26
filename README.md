# Akashic

Universal file index — hybrid Go scanner + Python API + Meilisearch + React web UI.

## Stack

- **Scanner** (Go) — walks filesystems, hashes content, posts batches to the API.
- **API** (FastAPI / Python 3.12) — ingest, search, RBAC, scheduling, webhooks.
- **Web** (React + Vite + Tailwind) — admin dashboard.
- **Storage** — PostgreSQL, Meilisearch, Redis (extraction queue), Tika (text extraction).

## Run

Bring everything up with the bundled compose stack:

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

The API reads `.env` from the repo root (see [compose.yaml](compose.yaml) `api.env_file`).
Notable keys:

| Key | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://…` | Postgres DSN |
| `MEILI_URL` / `MEILI_KEY` | local | Meilisearch endpoint |
| `REDIS_URL` | local | Extraction queue backend |
| `SECRET_KEY` | `changeme-secret-key` | JWT signing key |
| `STALE_SCAN_THRESHOLD_MINUTES` | `60` | After this many minutes, the watchdog marks pending/running scans as failed and frees the source for re-scan |

## First-time login

The first user to register at `POST /api/users/register` becomes admin; registration
closes after that. To reset the admin password without losing data:

```sh
docker compose exec api python -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('newpassword'))"
docker compose exec postgres psql -U akashic -d akashic \
  -c "UPDATE users SET password_hash = '<paste hash>' WHERE username = 'admin';"
```
