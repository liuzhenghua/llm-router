import asyncio

import pytest

from llm_router.services.cache.core.dual_cache import DualCache
from llm_router.services.cache.core.in_memory_cache import InMemoryCache
from llm_router.services.cache.api_key_cache import ApiKeyCache
from llm_router.services.cache.degraded_cache import DegradedRouteCache, DegradedType


class _RedisMissCache:
    is_available = True

    async def get(self, key: str):
        return None

    async def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        return True

    async def delete(self, key: str) -> bool:
        return True


class _RedisSpyCache:
    is_available = True

    def __init__(self):
        self.last_ttl = None

    async def get(self, key: str):
        return None

    async def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        self.last_ttl = ttl
        return True

    async def delete(self, key: str) -> bool:
        return True


@pytest.mark.asyncio
async def test_add_degraded_route_uses_explicit_ttl_instead_of_memory_default():
    memory = InMemoryCache(default_ttl=1)
    cache = DualCache(
        settings=object(),
        in_memory_cache=memory,
        redis_cache=None,
        in_memory_ttl=1,
        redis_ttl=10,
    )

    degraded_cache = DegradedRouteCache(cache)
    await degraded_cache.mark_degraded(123, DegradedType.UNAVAILABLE)

    await asyncio.sleep(1.1)
    assert await degraded_cache.get_all_degraded_route_ids() == [123]


@pytest.mark.asyncio
async def test_increment_fail_count_does_not_expose_route_as_degraded_before_threshold():
    memory = InMemoryCache(default_ttl=10)
    cache = DualCache(
        settings=object(),
        in_memory_cache=memory,
        redis_cache=None,
        in_memory_ttl=10,
        redis_ttl=10,
    )

    degraded_cache = DegradedRouteCache(cache)

    assert await degraded_cache.increment_fail_count(123) == 1
    status = await degraded_cache.get_status(123)
    assert status is not None
    assert status.is_degraded is False
    assert status.fail_count == 1
    assert await degraded_cache.get_all_degraded_route_ids() == []


@pytest.mark.asyncio
async def test_recover_clears_pending_fail_count_for_non_degraded_route():
    memory = InMemoryCache(default_ttl=10)
    cache = DualCache(
        settings=object(),
        in_memory_cache=memory,
        redis_cache=None,
        in_memory_ttl=10,
        redis_ttl=10,
    )

    degraded_cache = DegradedRouteCache(cache)

    assert await degraded_cache.increment_fail_count(123) == 1
    assert await degraded_cache.recover(123) is False
    assert await degraded_cache.increment_fail_count(123) == 1


@pytest.mark.asyncio
async def test_generic_get_set_remove_uses_memory_layer():
    memory = InMemoryCache(default_ttl=10)
    cache = DualCache(
        settings=object(),
        in_memory_cache=memory,
        redis_cache=None,
        in_memory_ttl=10,
        redis_ttl=10,
    )

    await cache.set("example:key", {"ok": True})

    assert await cache.get("example:key") == {"ok": True}
    await cache.remove("example:key")
    assert await cache.get("example:key") is None


@pytest.mark.asyncio
async def test_prefer_redis_without_memory_fallback_clears_stale_memory():
    memory = InMemoryCache(default_ttl=10)
    cache = DualCache(
        settings=object(),
        in_memory_cache=memory,
        redis_cache=_RedisMissCache(),
        in_memory_ttl=10,
        redis_ttl=10,
    )
    await memory.set("logical:public", [{"id": 1, "name": "stale"}], ttl=10)

    result = await cache.get("logical:public", prefer_redis=True, fallback_to_memory=False)

    assert result is None
    assert await memory.get("logical:public") is None


@pytest.mark.asyncio
async def test_default_memory_and_redis_ttls_are_separate():
    memory = InMemoryCache(default_ttl=999)
    redis = _RedisSpyCache()
    cache = DualCache(
        settings=object(),
        in_memory_cache=memory,
        redis_cache=redis,
    )

    await cache.set("example:key", {"ok": True})

    assert redis.last_ttl == 600
    await asyncio.sleep(1.1)
    assert await memory.get("example:key") == {"ok": True}


@pytest.mark.asyncio
async def test_api_key_cache_invalidate_all_removes_hash_prefix():
    memory = InMemoryCache(default_ttl=10)
    cache = DualCache(
        settings=object(),
        in_memory_cache=memory,
        redis_cache=None,
        in_memory_ttl=10,
        redis_ttl=10,
    )
    api_key_cache = ApiKeyCache(cache)

    await api_key_cache.set_by_hash("hash-1", {"id": 1})
    await api_key_cache.set_by_hash("hash-2", {"id": 2})
    await cache.set("other:hash-1", {"id": 3})

    await api_key_cache.invalidate_all()

    assert await api_key_cache.get_by_hash("hash-1") is None
    assert await api_key_cache.get_by_hash("hash-2") is None
    assert await cache.get("other:hash-1") == {"id": 3}
