# Alembic Database Migrations

This project uses [Alembic](https://alembic.sqlalchemy.org/) for schema migration management, paired with an async SQLAlchemy engine. All migrations live in `migrations/versions/`.

---

## Directory layout

```
migrations/
  env.py              # Alembic runtime environment (async-compatible)
  script.py.mako      # Template for generated migration files
  versions/           # One .py file per revision
alembic.ini           # Alembic configuration
```

---

## How it works

`migrations/env.py` is pre-configured to:

- Read the database URL from project settings (`APP_MODE` / `.env`), so migrations always target the same database as the running application.
- Use SQLAlchemy's **async engine** (`async_engine_from_config`) for both local SQLite and server MySQL.
- Enable `render_as_batch=True` so that column / constraint changes on SQLite work transparently (SQLite does not support `ALTER COLUMN` natively; Alembic rewrites the table instead).

---

## Common commands

All commands must be run from the project root.

### Check current revision

```bash
uv run alembic current
```

### Show migration history

```bash
uv run alembic history --verbose
```

### Apply all pending migrations (upgrade to latest)

```bash
uv run alembic upgrade head
```

### Roll back the last migration

```bash
uv run alembic downgrade -1
```

### Roll back to a specific revision

```bash
uv run alembic downgrade <revision_id>
```

---

## Creating a new migration

After modifying any model in `src/llm_router/domain/models.py`, generate a migration automatically:

```bash
uv run alembic revision --autogenerate -m "describe your change here"
```

Alembic will compare the current models against the live database and emit the required `op.*` calls. Always **review the generated file** in `migrations/versions/` before applying it — autogenerate is helpful but not perfect (e.g., it cannot detect column renames).

Then apply it:

```bash
uv run alembic upgrade head
```

---

## Introducing Alembic to an existing deployment

If a database was created before Alembic was added (e.g., via the legacy `init_db()` / `create_all` path), stamp it so Alembic treats it as up-to-date without re-running migrations:

```bash
uv run alembic stamp head
```

This writes the current revision into the `alembic_version` table without executing any SQL DDL.

---

## Generating SQL scripts (offline mode)

To produce a plain SQL script instead of running against a live database — useful for code review or DBA approval:

```bash
uv run alembic upgrade head --sql > migrations/upgrade_head.sql
```

---

## Configuration reference

| File | Key setting | Purpose |
|------|-------------|---------|
| `alembic.ini` | `script_location` | Points Alembic to `migrations/` |
| `alembic.ini` | `sqlalchemy.url` | Placeholder; overridden by `env.py` at runtime |
| `migrations/env.py` | `config.set_main_option(...)` | Reads `effective_database_url` from `get_settings()` |
| `migrations/env.py` | `render_as_batch=True` | Enables table-rebuild strategy for SQLite ALTER ops |

The active database URL is determined by `APP_MODE` in `.env`:

| `APP_MODE` | URL |
|------------|-----|
| `local` (default) | `sqlite+aiosqlite:///data/llm_router.db` |
| `server` | `mysql+asyncmy://<MYSQL_USER>:<MYSQL_PASSWORD>@<MYSQL_HOST>:<MYSQL_PORT>/<MYSQL_DATABASE>` |
