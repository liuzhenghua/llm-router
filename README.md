# LLM Router

> A lightweight yet production-ready LLM gateway — start with a single `uv run` and SQLite, scale to MySQL + Redis without changing a line of application code.

**Drop-in compatible** with the OpenAI and Anthropic API. Point your existing SDK at `http://your-host/v1` and it just works.

| | Local mode | Server mode |
|---|---|---|
| Storage | SQLite (file, zero setup) | MySQL (shared across instances) |
| Cache | In-memory LRU | In-memory LRU + Redis |
| Deployment | Single process | Multi-instance / containerized |
| Dependencies | None | MySQL + Redis |

---

## Screenshots

### Dashboard — real-time overview of requests, balance, and daily spend

![dashboard](/docs/screenshots/dashboard.png)

### Logical Models & Routes — map a model name to one or more backend providers with priority fallback

![routes](/docs/screenshots/routes.png)

### Request Detail — per-request token breakdown, cost split, latency, and optional full content log

![request detail](/docs/screenshots/request_details.png)

---

## Features

- **Protocol compatibility** — serves OpenAI `POST /v1/chat/completions` and Anthropic `POST /v1/messages`
- **Logical model routing** — expose a stable model name (e.g. `gpt-4o`) and route it to any number of real backend providers
- **Priority fallback** — if the top-priority provider fails, the gateway automatically tries the next one
- **Per-key quota control** — balance, daily spend cap, QPS limit, and allowed-model list per API key
- **Accurate billing** — per-request cost breakdown: input, output, cache-read, and cache-write, priced at creation time so history is never affected by price changes
- **Prompt cache awareness** — handles `cache_read_tokens` and `cache_write_tokens` so cached tokens are never double-billed
- **Streaming support** — transparent SSE pass-through for both OpenAI and Anthropic streaming
- **Audit logging** — optional per-key request/response content capture; metadata always recorded
- **Flexible deployment** — defaults to SQLite + in-memory cache with zero external dependencies; enable MySQL (`USE_MYSQL=true`) and/or Redis (`REDIS_ENABLED=true`) independently to scale to multi-instance, production deployments — same codebase, no code changes required
- **Built-in admin panel** — manage keys, providers, routes, and view logs and billing without any extra tooling

---

## Architecture

### System Overview

```mermaid
graph TB
    ClientA[OpenAI SDK / App] -->|POST /v1/chat/completions| GW[llm-router Gateway]
    ClientB[Anthropic SDK / App] -->|POST /v1/messages| GW

    GW --> Auth[API Key Validation\nBalance · Daily Limit · QPS]
    Auth --> Router[Route Selector\nPriority · Protocol Filter]

    Router --> P1[Provider 1\nopenai protocol]
    Router --> P2[Provider 2\nanthropic protocol]
    Router --> P3[Provider N\ncustom endpoint]

    GW --> Cache[Dual Cache\nIn-Memory LRU + Redis]
    GW --> DB[(SQLite / MySQL\nLogs · Billing · Config)]
```

### Request Lifecycle

```mermaid
sequenceDiagram
    participant C as Client
    participant G as Gateway
    participant A as Auth & Quota
    participant R as Router
    participant P as Upstream Provider
    participant DB as Database

    C->>G: API request (OpenAI or Anthropic format)
    G->>A: Validate API Key (cache-first)
    A->>A: Check balance, daily limit, QPS, model access
    G->>R: Lookup routes for logical model
    R->>P: Forward to highest-priority provider
    alt success
        P-->>G: Response / SSE stream
        G->>DB: Write request log + usage record + billing
        G-->>C: Return response
    else provider failure
        R->>P: Retry next provider by priority
        P-->>G: Response or final failure
        G->>DB: Write failed request log
        G-->>C: Return error
    end
```

### Dual Cache (In-Memory + Redis)

```mermaid
graph LR
    Request --> L1[In-Memory LRU]
    L1 -->|miss| L2[Redis Cache]
    L2 -->|miss| DB[(Database)]
    L2 -->|hit + backfill| L1
    DB -->|result| L2
    DB -->|result| L1
```

The cache stores API key metadata and route configurations. Redis is optional — if unreachable, the gateway falls back to in-memory transparently.

---

## Quick Start

### 1. Install dependencies

```bash
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — set APP_ENCRYPTION_KEY and SESSION_SECRET at minimum
```

### 3. Create an admin account

```bash
uv run llm-router init-admin --username admin --password your-password
```

### 4. Start the server

```bash
uv run uvicorn llm_router.main:app --reload
```

For breakpoint debugging:

```bash
uv run python -m llm_router.main
```

### 5. Open the admin panel

[http://127.0.0.1:8000/admin/login](http://127.0.0.1:8000/admin/login)

---

## Docker

### Local mode (SQLite — zero dependencies)

```bash
cd docker/local
docker compose up --build
```

Then create an admin account:

```bash
docker compose exec llm-router uv run llm-router init-admin --username admin --password your-password
```

### Server mode (MySQL + Redis)

```bash
cd docker/server
docker compose up --build
```

Then create an admin account:

```bash
docker compose exec llm-router uv run llm-router init-admin --username admin --password your-password
```

> Tables are created automatically on first startup via `Base.metadata.create_all`. No manual migration step needed.

---

## Configuration

Key environment variables (see `.env.example` for the full list):

| Variable | Default | Description |
|---|---|---|
| `USE_MYSQL` | `false` | Set to `true` to use MySQL instead of SQLite |
| `REDIS_ENABLED` | `false` | Set to `true` to enable Redis cache, queue, and distributed lock |
| `APP_ENCRYPTION_KEY` | — | Fernet key used to encrypt upstream provider API keys |
| `SESSION_SECRET` | — | Secret for admin session cookies |
| `DATABASE_URL` | — | Override SQLAlchemy DB URL directly (skips `USE_MYSQL` auto-build) |
| `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | — | MySQL connection settings (used when `USE_MYSQL=true`) |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` / `REDIS_PASSWORD` | — | Redis connection settings (used when `REDIS_ENABLED=true`) |
| `TABLE_PREFIX` | `""` | Optional prefix for all table names, e.g. `lr_` → `lr_api_keys` |

---

## Core Concepts

### Logical Model

The model name your clients send (e.g. `gpt-4o`, `claude-sonnet`, `my-internal-model`). Clients never need to know which actual provider is behind it.

### Provider Model

A concrete upstream endpoint — provider type, protocol (`openai` or `anthropic`), model name, API key, and per-million-token pricing.

### Route

A mapping from a logical model to one or more provider models, each with a priority. The gateway selects by priority and falls back automatically on failure.

---

## API Compatibility

| Endpoint | Protocol | Streaming |
|---|---|---|
| `POST /v1/chat/completions` | OpenAI | ✅ SSE |
| `POST /v1/messages` | Anthropic | ✅ streaming |

Usage with the OpenAI Python SDK:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://your-host/v1",
    api_key="your-llm-router-key",
)

response = client.chat.completions.create(
    model="gpt-4o",   # your logical model name
    messages=[{"role": "user", "content": "Hello"}],
)
```

---

## Project Structure

```
llm-router/
  src/llm_router/
    api/          # FastAPI route handlers (openai, anthropic, admin)
    core/         # Config, database, security
    domain/       # ORM models, schemas, enums
    services/     # Gateway, router, billing, cache, streaming handlers
    templates/    # Jinja2 admin UI templates
  docker/
    local/        # SQLite Compose
    server/       # MySQL Compose + init.sql
  docs/
    architecture/ # Architecture documentation
    tests/
```

---

## License

MIT
