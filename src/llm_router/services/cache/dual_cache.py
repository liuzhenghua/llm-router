from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from llm_router.core.config import AppMode
from llm_router.services.cache.in_memory_cache import InMemoryCache
from llm_router.services.cache.redis_cache import RedisCache
from llm_router.services.cache.serializer import CacheSerializer

if TYPE_CHECKING:
    from llm_router.core.config import Settings

logger = logging.getLogger(__name__)


class DualCache:
    """
    双层缓存：InMemory + Redis (server 模式)

    默认读取策略：内存 → Redis → None（回填到内存）
    计费相关读取策略：Redis → 内存 → None（优先跨 pod 一致性）
    写入策略：同时写内存（in_memory_ttl）+ Redis（redis_ttl）
    """

    # Key 模板
    KEY_APIKEY_HASH = "apikey:hash:{key_hash}"
    KEY_APIKEY_ID = "apikey:id:{id}"
    KEY_ROUTE_LOGICAL = "route:logical:{logical_model_id}"
    KEY_PROVIDER_ID = "provider:id:{id}"
    KEY_DEGRADED_ROUTES = "route:degraded:set"  # 存储所有降级路由 ID 的集合

    def __init__(
        self,
        settings: Settings,
        in_memory_cache: InMemoryCache,
        redis_cache: RedisCache | None,
        in_memory_ttl: int = 60,
        redis_ttl: int = 3600,
    ):
        self._settings = settings
        self._memory = in_memory_cache
        self._redis = redis_cache
        self._serializer = CacheSerializer()
        self._in_memory_ttl = in_memory_ttl
        self._redis_ttl = redis_ttl

    @property
    def is_server_mode(self) -> bool:
        return self._settings.app_mode == AppMode.SERVER

    # ==================== ApiKey 操作 ====================

    async def get_apikey_by_hash(self, key_hash: str) -> dict | None:
        """
        通过 key_hash 获取 ApiKey 缓存数据（计费敏感）

        读取策略：Redis（跨 pod 余额一致性）→ 内存 → None

        Returns:
            dict 或 None（包含 id, name, status, balance, daily_spend_amount 等字段）
        """
        cache_key = self.KEY_APIKEY_HASH.format(key_hash=key_hash)

        # 1. 优先读 Redis（server 模式，计费数据跨 pod 一致性）
        if self._redis and self._redis.is_available:
            raw = await self._redis.get(cache_key)
            if raw:
                cached = self._serializer.deserialize(raw)
                # 回填内存
                await self._memory.set(cache_key, cached, self._in_memory_ttl)
                return cached

        # 2. 降级读内存
        data = await self._memory.get(cache_key)
        if data is not None:
            return data

        return None

    async def set_apikey(self, key_hash: str, data: dict) -> None:
        """缓存 ApiKey 数据（内存 TTL + Redis TTL 分开）"""
        cache_key = self.KEY_APIKEY_HASH.format(key_hash=key_hash)

        # 写内存
        await self._memory.set(cache_key, data, self._in_memory_ttl)

        # 写 Redis (server 模式)
        if self._redis and self._redis.is_available:
            raw = self._serializer.serialize(data)
            await self._redis.set(cache_key, raw, self._redis_ttl)

    async def invalidate_apikey(self, key_hash: str, api_key_id: int | None = None) -> None:
        """失效 ApiKey 缓存"""
        cache_key = self.KEY_APIKEY_HASH.format(key_hash=key_hash)
        await self._memory.delete(cache_key)
        if self._redis and self._redis.is_available:
            await self._redis.delete(cache_key)

        if api_key_id:
            id_key = self.KEY_APIKEY_ID.format(id=api_key_id)
            await self._memory.delete(id_key)
            if self._redis and self._redis.is_available:
                await self._redis.delete(id_key)

    # ==================== Route 操作 ====================

    async def get_routes_by_logical_model(self, logical_model_id: int) -> list[dict] | None:
        """获取某逻辑模型的路由列表（内存优先）"""
        cache_key = self.KEY_ROUTE_LOGICAL.format(logical_model_id=logical_model_id)

        # 1. 读内存
        data = await self._memory.get(cache_key)
        if data is not None:
            return data

        # 2. 读 Redis，回填内存
        if self._redis and self._redis.is_available:
            raw = await self._redis.get(cache_key)
            if raw:
                cached = self._serializer.deserialize(raw)
                await self._memory.set(cache_key, cached, self._in_memory_ttl)
                return cached

        return None

    async def set_routes(self, logical_model_id: int, routes: list[dict]) -> None:
        """缓存路由列表（内存 TTL + Redis TTL 分开）"""
        cache_key = self.KEY_ROUTE_LOGICAL.format(logical_model_id=logical_model_id)

        await self._memory.set(cache_key, routes, self._in_memory_ttl)
        if self._redis and self._redis.is_available:
            raw = self._serializer.serialize(routes)
            await self._redis.set(cache_key, raw, self._redis_ttl)

    async def invalidate_routes(self, logical_model_id: int) -> None:
        """失效路由缓存"""
        cache_key = self.KEY_ROUTE_LOGICAL.format(logical_model_id=logical_model_id)
        await self._memory.delete(cache_key)
        if self._redis and self._redis.is_available:
            await self._redis.delete(cache_key)

    # ==================== Provider 操作 ====================

    async def get_provider(self, provider_id: int) -> dict | None:
        """获取 Provider 缓存数据（内存优先）"""
        cache_key = self.KEY_PROVIDER_ID.format(id=provider_id)

        # 1. 读内存
        data = await self._memory.get(cache_key)
        if data is not None:
            return data

        # 2. 读 Redis，回填内存
        if self._redis and self._redis.is_available:
            raw = await self._redis.get(cache_key)
            if raw:
                cached = self._serializer.deserialize(raw)
                await self._memory.set(cache_key, cached, self._in_memory_ttl)
                return cached

        return None

    async def set_provider(self, provider_id: int, data: dict) -> None:
        """缓存 Provider 数据（内存 TTL + Redis TTL 分开）"""
        cache_key = self.KEY_PROVIDER_ID.format(id=provider_id)

        await self._memory.set(cache_key, data, self._in_memory_ttl)
        if self._redis and self._redis.is_available:
            raw = self._serializer.serialize(data)
            await self._redis.set(cache_key, raw, self._redis_ttl)

    async def invalidate_provider(self, provider_id: int) -> None:
        """失效 Provider 缓存"""
        cache_key = self.KEY_PROVIDER_ID.format(id=provider_id)
        await self._memory.delete(cache_key)
        if self._redis and self._redis.is_available:
            await self._redis.delete(cache_key)

    # ==================== Degraded Routes 操作 ====================

    async def add_degraded_route(self, route_id: int) -> None:
        """添加降级路由到集合"""
        # 1. 先读写内存（LOCAL 模式）
        ids = await self._memory.get(self.KEY_DEGRADED_ROUTES) or set()
        ids.add(route_id)
        await self._memory.set(self.KEY_DEGRADED_ROUTES, ids)

        # 2. 再写 Redis（server 模式）
        if self._redis and self._redis.is_available:
            await self._redis._client.sadd(self.KEY_DEGRADED_ROUTES, route_id)

    async def remove_degraded_route(self, route_id: int) -> None:
        """从降级集合中移除路由"""
        # 1. 先读写内存（LOCAL 模式）
        ids = await self._memory.get(self.KEY_DEGRADED_ROUTES) or set()
        ids.discard(route_id)
        await self._memory.set(self.KEY_DEGRADED_ROUTES, ids)

        # 2. 再写 Redis（server 模式）
        if self._redis and self._redis.is_available:
            await self._redis._client.srem(self.KEY_DEGRADED_ROUTES, route_id)

    async def get_all_degraded_route_ids(self) -> list[int]:
        """获取所有降级路由 ID"""
        # 1. 优先读内存
        ids = await self._memory.get(self.KEY_DEGRADED_ROUTES) or set()
        if ids:
            return list(ids)

        # 2. 回读 Redis（server 模式）
        if self._redis and self._redis.is_available:
            members = await self._redis._client.smembers(self.KEY_DEGRADED_ROUTES)
            return [int(m) for m in members]

        return []


# 全局实例（lifespan 中初始化）
dual_cache: DualCache | None = None


def get_dual_cache() -> DualCache | None:
    return dual_cache


def set_dual_cache(cache: DualCache) -> None:
    global dual_cache
    dual_cache = cache