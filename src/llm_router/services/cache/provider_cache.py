from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_router.services.cache.core.dual_cache import DualCache


class ProviderCache:
    """Provider 模型缓存。"""

    KEY_PROVIDER_ID = "provider:id:{id}"

    def __init__(self, dual_cache: DualCache):
        self._cache = dual_cache

    async def get(self, provider_id: int) -> dict | None:
        return await self._cache.get(self.KEY_PROVIDER_ID.format(id=provider_id))

    async def set(self, provider_id: int, data: dict) -> None:
        await self._cache.set(self.KEY_PROVIDER_ID.format(id=provider_id), data)

    async def invalidate(self, provider_id: int) -> None:
        await self._cache.remove(self.KEY_PROVIDER_ID.format(id=provider_id))


provider_cache: ProviderCache | None = None


def get_provider_cache() -> ProviderCache:
    assert provider_cache is not None, "ProviderCache is not initialized"
    return provider_cache


def set_provider_cache(cache: ProviderCache) -> None:
    global provider_cache
    provider_cache = cache
