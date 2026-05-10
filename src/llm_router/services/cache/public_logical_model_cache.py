from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_router.services.cache.core.dual_cache import DualCache


class PublicLogicalModelCache:
    """公共逻辑模型列表缓存。"""

    KEY_PUBLIC_LOGICAL_MODELS = "logical:public"

    def __init__(self, dual_cache: DualCache):
        self._cache = dual_cache

    async def get_all(self) -> list[dict] | None:
        return await self._cache.get(
            self.KEY_PUBLIC_LOGICAL_MODELS,
            prefer_redis=True,
            fallback_to_memory=False,
        )

    async def set_all(self, models: list[dict]) -> None:
        await self._cache.set(self.KEY_PUBLIC_LOGICAL_MODELS, models)

    async def invalidate(self) -> None:
        await self._cache.remove(self.KEY_PUBLIC_LOGICAL_MODELS)


public_logical_model_cache: PublicLogicalModelCache | None = None


def get_public_logical_model_cache() -> PublicLogicalModelCache:
    assert public_logical_model_cache is not None, "PublicLogicalModelCache is not initialized"
    return public_logical_model_cache


def set_public_logical_model_cache(cache: PublicLogicalModelCache) -> None:
    global public_logical_model_cache
    public_logical_model_cache = cache
