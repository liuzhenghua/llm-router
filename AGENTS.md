# llm-router Agent Guidelines

Project conventions and coding standards for AI agents and contributors.

---

## Tech Stack

- **Backend**: FastAPI + SQLAlchemy (async) + Pydantic Settings
- **DB**: SQLite (local mode) / MySQL (server mode)
- **Cache**: In-memory LRU + Redis (server mode)
- **Frontend**: Jinja2 templates + Tailwind CSS (CDN) + Vanilla JS
- **Runtime**: Python 3.12+, managed by `uv`

---

## Dual Cache (`services/cache/`)

The cache layer is a two-tier **DualCache**: `InMemoryCache` (always on) + `RedisCache` (server mode only).

### Read Strategy — cascade with backfill

```
In-Memory → Redis → None
```

On a Redis hit, **always backfill** the in-memory layer:

```python
data = await self._memory.get(cache_key)
if data is not None:
    return data

if self._redis and self._redis.is_available:
    raw = await self._redis.get(cache_key)
    if raw:
        cached = self._serializer.deserialize(raw)
        await self._memory.set(cache_key, cached, ttl)   # backfill
        return cached

return None
```

### Write Strategy — dual-write

Always write to **both** layers. Redis is gated by `is_available`:

```python
await self._memory.set(cache_key, data, ttl)

if self._redis and self._redis.is_available:
    raw = self._serializer.serialize(data)
    await self._redis.set(cache_key, raw, ttl)
```

### Invalidation

Delete from both layers in every `invalidate_*` method:

```python
await self._memory.delete(cache_key)
if self._redis and self._redis.is_available:
    await self._redis.delete(cache_key)
```

Call `invalidate_*` in `admin.py` whenever a resource (API Key, Provider, Route) is mutated.

### Key Naming Convention

All Redis keys are prefixed with `llm_router:cache:` (handled by `RedisCache._key()`).
Key templates are defined as class-level constants on `DualCache`:

```
apikey:hash:{key_hash}
apikey:id:{id}
route:logical:{logical_model_id}
provider:id:{id}
route:degraded:{route_id}
route:degraded:set
```

### Redis Graceful Degradation

`RedisCache` is always optional. If Redis is unreachable at startup or a command fails, `is_available` is set to `False` and the operation silently returns `None` / `False`. **Never let Redis errors propagate to the request path.**

---

## Pagination

Two patterns are used depending on data volume:

### Server-Side Pagination (large datasets — Request Logs)

Backend computes the pagination object and returns it to the template:

```python
per_page = settings.admin_page_size
total_pages = max(1, (total + per_page - 1) // per_page)
pagination = {
    "page": page,
    "pages": total_pages,
    "has_prev": page > 1,
    "has_next": page < total_pages,
    "prev_num": page - 1 if page > 1 else None,
    "next_num": page + 1 if page < total_pages else None,
}
```

Template renders prev/next links using a `build_query_string` macro that **preserves all active filter parameters** in the URL.

### Client-Side Pagination (small datasets — API Keys, Providers, Billing, Logical Models)

Data is embedded as JSON in the template. JS handles filtering + slicing:

```js
let currentPage = 1;
const rowsPerPage = 10;   // 5 for nested tables (e.g. routes modal)

function renderTable() {
  const filtered = allData.filter(/* apply search filters */);
  const totalPages = Math.ceil(filtered.length / rowsPerPage);
  const pageData = filtered.slice((currentPage - 1) * rowsPerPage, currentPage * rowsPerPage);
  // ... build table DOM
}
```

Rules:
- Reset `currentPage = 1` whenever a filter input changes.
- Disable the prev button when `currentPage === 1`, next when `currentPage === totalPages`.
- Nested tables inside modals use `rowsPerPage = 5`.

---

## Compact Tables (Tailwind)

All admin tables share a consistent compact style. **Do not use Bootstrap or custom CSS.**

### Container

```html
<div class="overflow-x-auto rounded-2xl bg-white shadow-sm">
```

### Table Element

```html
<table class="w-full text-sm text-left text-slate-500">
```

### Header Row

```html
<thead class="text-xs text-slate-700 uppercase bg-slate-50">
  <tr>
    <th scope="col" class="px-2 py-3 whitespace-nowrap">Column</th>
  </tr>
</thead>
```

### Body Rows

```html
<tr class="bg-white border-b">
  <td class="px-2 py-2 text-xs font-medium text-slate-900 whitespace-nowrap">Primary field</td>
  <td class="px-2 py-2 text-xs whitespace-nowrap">Regular field</td>
</tr>
```

Key classes:
- `px-2 py-2` — compact cell padding (smaller than default `px-4 py-3`)
- `text-xs` — all cell content uses `text-xs`
- `whitespace-nowrap` — prevent wrapping in cells with identifiers or numbers
- `border-b` — bottom border only, no full grid lines

### Status Badges

```html
<!-- active -->
<span class="inline-flex items-center rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-medium text-emerald-700">active</span>

<!-- disabled -->
<span class="inline-flex items-center rounded-full bg-rose-50 px-2.5 py-0.5 text-xs font-medium text-rose-700">disabled</span>
```

### Action Buttons (in table cells)

```html
<!-- Default edit -->
<button class="rounded-lg border border-slate-200 px-3 py-1.5 text-xs">编辑</button>

<!-- Danger / delete -->
<button class="rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs text-rose-700">禁用</button>

<!-- Success / topup -->
<button class="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs text-emerald-700">充值</button>

<!-- Info / routes -->
<button class="rounded-lg border border-sky-200 bg-sky-50 px-3 py-1.5 text-xs text-sky-700">路由</button>
```

### Search / Filter Bar

```html
<div class="mb-4 flex flex-wrap gap-2 items-end">
  <input class="w-full rounded-xl border border-slate-200 px-3 py-1.5 text-xs" placeholder="搜索...">
  <button class="rounded-xl bg-slate-800 px-3 py-1.5 text-xs text-white font-medium hover:bg-slate-700">搜索</button>
  <button class="rounded-xl border border-slate-300 px-3 py-1.5 text-xs text-slate-700 font-medium hover:bg-slate-50">重置</button>
</div>
```

### Color Palette Reference

| Semantic  | Tailwind family |
|-----------|----------------|
| Primary   | `slate-950` / `slate-800` |
| Border    | `slate-200` |
| Text body | `slate-500` |
| Text head | `slate-700` |
| Success   | `emerald-*` |
| Danger    | `rose-*` |
| Info      | `sky-*` |

---

## Database Migrations (`migrations/`)

Schema changes are **never** embedded in Python code. Every schema change must be written as a SQL file under `migrations/`.

### Versioned folder structure

Migrations are grouped by project version (read from the `VERSION` file at the repo root).

```
migrations/
  {version}/
    migration_mysql_{version}.sql    # MySQL variant
    migration_sqlite_{version}.sql   # SQLite variant
```

Example for version `1.0.1`:

```
migrations/
  1.0.1/
    migration_mysql_1.0.1.sql
    migration_sqlite_1.0.1.sql
```

- Always write to the folder that matches the **current** version in `VERSION`.
- When the version is bumped, create a new folder for the new version before adding any SQL.
- If the user explicitly says **not** to bump `VERSION`, keep writing to the current version's migration files and append the new SQL there instead of creating a new version folder.
- Always create both files (MySQL + SQLite). Split the SQL when syntax differs.

### File header (required)

Every migration file **must** start with the following header block. Replace `{version}` and fill in the description.

```sql
-- Migration: {version}
-- Description: <one-line summary of what this migration does>
--
-- !! IMPORTANT: If you configured a custom TABLE_PREFIX (default: "lr_"),
-- !! replace every occurrence of "lr_" in this file with your prefix
-- !! before running. Example: s/lr_/myprefix_/g
--
-- Apply (SQLite):
--   sqlite3 data/llm_router.db < migrations/{version}/migration_sqlite_{version}.sql
--
-- Apply (MySQL):
--   mysql -u llm_router -p llm_router < migrations/{version}/migration_mysql_{version}.sql
```

### SQLite vs MySQL differences to watch for

| Operation | SQLite | MySQL |
|-----------|--------|-------|
| Add column | `ALTER TABLE t ADD COLUMN c TEXT` | `ALTER TABLE t ADD COLUMN c LONGTEXT` |
| Rename column | not supported < 3.25; use `CREATE TABLE … AS SELECT` | `ALTER TABLE t RENAME COLUMN old TO new` |
| Boolean | `INTEGER` (0/1) | `TINYINT(1)` |
| Large text | `TEXT` | `LONGTEXT` |

### Rules

- **Never** run `ALTER TABLE` or schema changes from Python application code.
- **Never** rely on SQLAlchemy `create_all()` to add new columns — it only creates missing tables.
- By default, generated migration SQL should include **schema changes only**. Do **not** include data migration SQL (`UPDATE`, backfill, transform, copy, or cleanup statements) unless the user explicitly asks for it or confirms that it is needed.
- Migration SQL should be appended to the current version's migration files while that version is still being worked on. Do not rewrite historical migrations for older released versions; use a new version folder for follow-up fixes after a version bump.
- Keep migrations idempotent where possible (e.g. `ADD COLUMN IF NOT EXISTS` on MySQL 8+).
- Default `TABLE_PREFIX` is `lr_`. All table names in migration files use this prefix unless overridden.

---

## General Conventions

- All admin pages extend `base.html` and fill the `page_content` block.
- Set `nav_active` to the matching nav key (`dashboard`, `api_keys`, `logical_models`, `providers`, `requests`, `billing`) so the sidebar highlights correctly.
- Use `openModal(id)` / `closeModal(id)` / `fillForm(formId, values)` from `base.html` for modals.
- Toast notifications: call `showToast(message, type)` with `type` = `success | error | info`.
- Async DB sessions are provided via `request.state.db` (SQLAlchemy `AsyncSession`).
- Do not import `dual_cache` directly in route handlers — use `get_dual_cache()` to access the global instance.
