from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_router.services.cache.core.dual_cache import DualCache


class ApiKeyCache:
    """API Key 缓存。"""

    KEY_APIKEY_HASH = "apikey:hash:{key_hash}"
    PREFIX_APIKEY_HASH = "apikey:hash:"

    def __init__(self, dual_cache: DualCache):
        self._cache = dual_cache

    async def get_by_hash(self, key_hash: str) -> dict | None:
        return await self._cache.get(self.KEY_APIKEY_HASH.format(key_hash=key_hash), prefer_redis=True)

    async def set_by_hash(self, key_hash: str, data: dict) -> None:
        await self._cache.set(self.KEY_APIKEY_HASH.format(key_hash=key_hash), data)

    async def invalidate(self, key_hash: str) -> None:
        await self._cache.remove(self.KEY_APIKEY_HASH.format(key_hash=key_hash))

    async def invalidate_all(self) -> None:
        await self._cache.remove_by_prefix(self.PREFIX_APIKEY_HASH)


api_key_cache: ApiKeyCache | None = None


def get_api_key_cache() -> ApiKeyCache:
    assert api_key_cache is not None, "ApiKeyCache is not initialized"
    return api_key_cache


def set_api_key_cache(cache: ApiKeyCache) -> None:
    global api_key_cache
    api_key_cache = cache
