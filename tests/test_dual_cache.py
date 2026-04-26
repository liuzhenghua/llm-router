import asyncio

import pytest

from llm_router.services.cache.dual_cache import DualCache
from llm_router.services.cache.in_memory_cache import InMemoryCache


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

    await cache.add_degraded_route(123, ttl=2)

    await asyncio.sleep(1.1)
    assert await cache.get_all_degraded_route_ids() == [123]
