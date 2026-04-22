# Dual Cache Design

## Overview

The dual cache is a two-tier caching layer combining **InMemoryCache** (always on) and **RedisCache** (server mode only).

```
InMemoryCache  —  per-process LRU, fast, short-lived (60 s)
RedisCache     —  shared across pods, slower, long-lived (3600 s)
```

---

## TTL Configuration

| Setting | Default | Description |
|---|---|---|
| `default_in_memory_ttl` | `60` s | In-memory cache TTL |
| `default_redis_ttl` | `3600` s | Redis cache TTL |

---

## Read Strategies

### Default — In-Memory First (routing config)

Used for: **Routes**, **Providers**

```
In-Memory → Redis → None
```

- Check in-memory cache first (lowest latency).
- On miss, check Redis (if available); backfill in-memory with `in_memory_ttl` on hit.
- Return `None` if both miss (caller fetches from DB and calls `set_*`).

### Billing-Sensitive — Redis First (API Key / balance)

Used for: **ApiKey** (contains `balance`, `daily_spend_amount`)

```
Redis → In-Memory → None
```

- Check Redis first to ensure cross-pod balance consistency in server mode.
- On miss (or Redis unavailable), fall back to in-memory.
- On Redis hit, backfill in-memory with `in_memory_ttl`.
- Return `None` if both miss.

---

## Write Strategy — Dual Write

All `set_*` methods write to both layers simultaneously:

```python
await self._memory.set(cache_key, data, self._in_memory_ttl)   # 60 s

if self._redis and self._redis.is_available:
    raw = self._serializer.serialize(data)
    await self._redis.set(cache_key, raw, self._redis_ttl)      # 3600 s
```

---

## Invalidation Strategy

All `invalidate_*` methods delete from both layers:

```python
await self._memory.delete(cache_key)
if self._redis and self._redis.is_available:
    await self._redis.delete(cache_key)
```

Call `invalidate_*` in `admin.py` whenever a resource (ApiKey, Provider, Route) is mutated.

---

## Key Naming

All Redis keys are prefixed with `llm_router:cache:` (handled by `RedisCache._key()`).

| Key Template | Resource |
|---|---|
| `apikey:hash:{key_hash}` | ApiKey (by hash) |
| `apikey:id:{id}` | ApiKey (by ID) |
| `route:logical:{logical_model_id}` | Route list for a logical model |
| `provider:id:{id}` | Provider |
| `route:degraded:set` | Set of all degraded route IDs |

---

## Redis Graceful Degradation

`RedisCache` is always optional. If Redis is unreachable at startup or a command fails:

- `is_available` is set to `False`.
- All Redis operations silently return `None` / `False`.
- The system continues using in-memory cache only.
- **Redis errors must never propagate to the request path.**

---

## Read Strategy Summary

| Resource | Read Order | Reason |
|---|---|---|
| ApiKey | Redis → Memory | Balance freshness across pods |
| Routes | Memory → Redis | Routing config, latency-sensitive |
| Providers | Memory → Redis | Config data, latency-sensitive |
| Degraded Routes | Memory → Redis | Local mode compatible |
