from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_router.services.cache.core.dual_cache import DualCache


class RouteCache:
    """逻辑模型路由缓存。"""

    KEY_ROUTE_LOGICAL = "route:logical:{logical_model_id}"

    def __init__(self, dual_cache: DualCache):
        self._cache = dual_cache

    async def get_by_logical_model(self, logical_model_id: int) -> list[dict] | None:
        return await self._cache.get(self.KEY_ROUTE_LOGICAL.format(logical_model_id=logical_model_id))

    async def set_by_logical_model(self, logical_model_id: int, routes: list[dict]) -> None:
        await self._cache.set(self.KEY_ROUTE_LOGICAL.format(logical_model_id=logical_model_id), routes)

    async def invalidate(self, logical_model_id: int) -> None:
        await self._cache.remove(self.KEY_ROUTE_LOGICAL.format(logical_model_id=logical_model_id))


route_cache: RouteCache | None = None


def get_route_cache() -> RouteCache:
    assert route_cache is not None, "RouteCache is not initialized"
    return route_cache


def set_route_cache(cache: RouteCache) -> None:
    global route_cache
    route_cache = cache
