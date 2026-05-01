# Database migrations

The api uses [Alembic](https://alembic.sqlalchemy.org) for all schema changes.

## How it runs

`akashic.database.ensure_schema()` is invoked from the FastAPI lifespan at startup. It handles three cases:

1. **Fresh DB** — empty database, no `alembic_version` table. `alembic upgrade head` creates everything.
2. **Existing DB from before Alembic was wired up** — tables already present (created via `Base.metadata.create_all`), no `alembic_version` row. The function stamps the DB at the baseline revision (`0001_baseline`) and then runs `upgrade head` (a no-op until later migrations exist).
3. **DB already managed by Alembic** — `alembic upgrade head` applies any new revisions and is idempotent if already at head.

The discriminator between cases 1 and 2 is the presence of the `users` table.

## Adding a migration

When you change a SQLAlchemy model:

```bash
# 1. Generate the migration against a *fresh, empty* DB so Alembic compares
#    against your models, not against an already-modified schema.
docker compose exec postgres psql -U akashic -d postgres -c "DROP DATABASE IF EXISTS akashic_alembic_gen; CREATE DATABASE akashic_alembic_gen;"

docker compose exec \
  -e DATABASE_URL="postgresql+asyncpg://akashic:changeme@postgres:5432/akashic_alembic_gen" \
  api \
  alembic revision --autogenerate -m "what this migration does"

docker compose exec postgres psql -U akashic -d postgres -c "DROP DATABASE akashic_alembic_gen;"

# 2. Move the generated file out of the container into the repo.
docker cp akashic-api-1:/app/alembic/versions/<generated_file>.py \
  api/alembic/versions/<NNNN>_<short_description>.py

# 3. Review the generated file. Alembic's autogenerate is famously imperfect with:
#    - JSONB defaults
#    - server_default values that look like Python (e.g., `text('now()')`)
#    - composite indexes
#    - certain index types (GIN/GiST in particular)
#    Compare against `\d <table_name>` output in psql before committing.

# 4. Optionally rename the revision string inside the file to something
#    semantic — e.g., from "8a3f2b1d…" to "0002_scan_snapshots". The
#    filename and the `revision:` variable should match.

# 5. Test the migration end-to-end:
#    - Spin up a fresh DB and run `alembic upgrade head` → tables look right.
#    - On a DB that's already at the previous head, run `alembic upgrade head` → idempotent.
#    - Optionally test `alembic downgrade -1` if you've written a real downgrade.
```

## Common pitfalls

- **Hand-edit the generated file** when autogenerate produces something silly.
- **Composite indexes** sometimes get split into separate single-column indexes by autogenerate; fix those by hand.
- **Server-side defaults** (`server_default=sa.text('now()')`) need to be preserved verbatim — they don't show up reliably from autogenerate when the DB is already populated.
- **Don't drop columns lightly.** A dropped column means rolling forward only — the downgrade can't restore data. If you absolutely need to drop, write a separate "soft delete" migration (rename to `_deprecated_<col>`) followed weeks later by a hard-drop migration.
- **GIN indexes** on array/JSONB columns must be created with `postgresql_using='gin'` — autogenerate tends to omit this.

## Deployment

Migrations run automatically when the api container starts. Production deploy procedure:

1. Merge a PR containing a new migration file.
2. Build a new api image (CI does this).
3. Deploy: container starts → `ensure_schema()` runs → migration applies → api becomes ready.

If a migration is destructive or long-running, consider a two-deploy strategy: ship the migration as a no-op rename in v1, run the data-shaping migration in v2 once v1 is stable.

## Where to find things

- [api/alembic.ini](../api/alembic.ini) — Alembic CLI config. The `sqlalchemy.url` is intentionally not set here; `alembic/env.py` reads it from `settings.database_url`.
- [api/alembic/env.py](../api/alembic/env.py) — Alembic environment, async-aware.
- [api/alembic/versions/](../api/alembic/versions/) — migration files.
- [api/akashic/database.py `ensure_schema()`](../api/akashic/database.py) — the lifespan hook.
- [api/tests/test_alembic_baseline.py](../api/tests/test_alembic_baseline.py) — coverage for the three startup cases.
