from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from llm_router.services.cache.core.in_memory_cache import InMemoryCache
from llm_router.services.cache.core.redis_cache import RedisCache
from llm_router.services.cache.core.serializer import CacheSerializer

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

    def __init__(
        self,
        settings: Settings,
        in_memory_cache: InMemoryCache,
        redis_cache: RedisCache | None,
        in_memory_ttl: int = 60,
        redis_ttl: int = 600,
    ):
        self._settings = settings
        self._memory = in_memory_cache
        self._redis = redis_cache
        self._serializer = CacheSerializer()
        self._in_memory_ttl = in_memory_ttl
        self._redis_ttl = redis_ttl

    # ==================== 通用操作 ====================

    async def get(
        self,
        key: str,
        *,
        prefer_redis: bool = False,
        fallback_to_memory: bool = True,
        backfill_memory_ttl: int | None = None,
    ):
        """读取缓存。

        默认读取策略：内存 → Redis → None。
        prefer_redis=True 时：Redis → 内存 → None，适合余额等跨 pod 一致性优先的数据。
        fallback_to_memory=False 时，Redis 未命中会清理本地内存并返回 None。
        """
        memory_ttl = backfill_memory_ttl if backfill_memory_ttl is not None else self._in_memory_ttl

        if prefer_redis:
            if self._redis and self._redis.is_available:
                data = await self._get_from_redis(key, backfill_memory_ttl=memory_ttl)
                if data is not None:
                    return data
                if not fallback_to_memory:
                    await self._memory.delete(key)
                    return None
            return await self._memory.get(key)

        data = await self._memory.get(key)
        if data is not None:
            return data
        return await self._get_from_redis(key, backfill_memory_ttl=memory_ttl)

    async def set(
        self,
        key: str,
        value,
        *,
        memory_ttl: int | None = None,
        redis_ttl: int | None = None,
    ) -> None:
        """写入双层缓存。"""
        await self._memory.set(key, value, memory_ttl if memory_ttl is not None else self._in_memory_ttl)
        if self._redis and self._redis.is_available:
            raw = self._serializer.serialize(value)
            await self._redis.set(key, raw, redis_ttl if redis_ttl is not None else self._redis_ttl)

    async def remove(self, key: str) -> None:
        """删除双层缓存。"""
        await self._memory.delete(key)
        if self._redis and self._redis.is_available:
            await self._redis.delete(key)

    async def remove_by_prefix(self, prefix: str) -> None:
        """按 key 前缀删除双层缓存。"""
        await self._memory.delete_by_prefix(prefix)
        if self._redis and self._redis.is_available:
            await self._redis.delete_by_prefix(prefix)

    async def _get_from_redis(self, key: str, *, backfill_memory_ttl: int):
        if self._redis and self._redis.is_available:
            raw = await self._redis.get(key)
            if raw:
                cached = self._serializer.deserialize(raw)
                await self._memory.set(key, cached, backfill_memory_ttl)
                return cached
        return None

# 全局实例（lifespan 中初始化）
dual_cache: DualCache | None = None


def get_dual_cache() -> DualCache:
    assert dual_cache is not None, "DualCache is not initialized"
    return dual_cache


def set_dual_cache(cache: DualCache) -> None:
    global dual_cache
    dual_cache = cache
