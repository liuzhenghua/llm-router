"""
降级路由状态管理

使用 DualCache 存储路由降级状态，支持分布式部署。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_router.services.cache.dual_cache import DualCache


logger = logging.getLogger(__name__)


class DegradedType(str, Enum):
    """降级类型"""
    QUOTA_EXHAUSTED = "quota_exhausted"  # 429/403，配额/限流/权限问题
    UNAVAILABLE = "unavailable"  # 5xx/超时，连通性问题


@dataclass
class RouteDegradedStatus:
    """路由降级状态"""
    route_id: int
    degraded_type: DegradedType
    fail_count: int
    last_fail_time: float  # Unix timestamp


class DegradedRouteCache:
    """
    降级路由状态缓存

    使用 DualCache 存储降级状态，支持 local 和 distributed 部署模式。
    """

    KEY_ROUTE_DEGRADED = "route:degraded:{route_id}"
    DEFAULT_TTL = 3600  # 1小时过期

    # 降级阈值
    FAIL_COUNT_THRESHOLD = 5  # 连续失败 5 次后降级

    def __init__(self, dual_cache: DualCache):
        self._cache = dual_cache

    def _get_key(self, route_id: int) -> str:
        return self.KEY_ROUTE_DEGRADED.format(route_id=route_id)

    async def get_status(self, route_id: int) -> RouteDegradedStatus | None:
        """
        获取路由降级状态

        Returns:
            RouteDegradedStatus 或 None（未降级）
        """
        cache_key = self._get_key(route_id)

        # 读取内存
        data = await self._cache._memory.get(cache_key)
        if data is not None:
            return self._from_dict(route_id, data)

        # 读取 Redis
        if self._cache._redis and self._cache._redis.is_available:
            raw = await self._cache._redis.get(cache_key)
            if raw:
                from llm_router.services.cache.serializer import CacheSerializer
                serializer = CacheSerializer()
                cached = serializer.deserialize(raw)
                # 回填内存
                await self._cache._memory.set(cache_key, cached, self.DEFAULT_TTL)
                return self._from_dict(route_id, cached)

        return None

    async def mark_degraded(
        self,
        route_id: int,
        degraded_type: DegradedType,
        fail_count: int = FAIL_COUNT_THRESHOLD,
    ) -> None:
        """
        标记路由为降级状态

        Args:
            route_id: 路由 ID
            degraded_type: 降级类型
            fail_count: 当前失败计数
        """
        data = {
            "degraded_type": degraded_type.value,
            "fail_count": fail_count,
            "last_fail_time": time.time(),
        }

        cache_key = self._get_key(route_id)

        # 写内存
        await self._cache._memory.set(cache_key, data, self.DEFAULT_TTL)

        # 写 Redis
        if self._cache._redis and self._cache._redis.is_available:
            from llm_router.services.cache.serializer import CacheSerializer
            serializer = CacheSerializer()
            raw = serializer.serialize(data)
            await self._cache._redis.set(cache_key, raw, self.DEFAULT_TTL)
            # 添加到降级路由集合
            await self._cache.add_degraded_route(route_id)

        logger.info(f"Route {route_id} marked as degraded: type={degraded_type.value}, fail_count={fail_count}")

    async def recover(self, route_id: int) -> bool:
        """
        恢复路由为正常状态

        Returns:
            True 如果恢复成功，False 如果路由本来就不是降级状态
        """
        status = await self.get_status(route_id)
        if status is None:
            return False

        cache_key = self._get_key(route_id)

        # 删除内存
        await self._cache._memory.delete(cache_key)

        # 删除 Redis
        if self._cache._redis and self._cache._redis.is_available:
            await self._cache._redis.delete(cache_key)
            # 从降级路由集合中移除
            await self._cache.remove_degraded_route(route_id)

        logger.info(f"Route {route_id} recovered from degraded state")
        return True

    async def increment_fail_count(self, route_id: int) -> int:
        """
        增加失败计数

        Returns:
            新的失败计数
        """
        status = await self.get_status(route_id)
        if status is None:
            # 初始化
            data = {
                "degraded_type": DegradedType.UNAVAILABLE.value,
                "fail_count": 1,
                "last_fail_time": time.time(),
            }
            cache_key = self._get_key(route_id)
            await self._cache._memory.set(cache_key, data, self.DEFAULT_TTL)
            if self._cache._redis and self._cache._redis.is_available:
                from llm_router.services.cache.serializer import CacheSerializer
                serializer = CacheSerializer()
                raw = serializer.serialize(data)
                await self._cache._redis.set(cache_key, raw, self.DEFAULT_TTL)
            return 1

        new_count = status.fail_count + 1
        data = {
            "degraded_type": status.degraded_type.value,
            "fail_count": new_count,
            "last_fail_time": time.time(),
        }

        cache_key = self._get_key(route_id)
        await self._cache._memory.set(cache_key, data, self.DEFAULT_TTL)

        if self._cache._redis and self._cache._redis.is_available:
            from llm_router.services.cache.serializer import CacheSerializer
            serializer = CacheSerializer()
            raw = serializer.serialize(data)
            await self._cache._redis.set(cache_key, raw, self.DEFAULT_TTL)

        return new_count

    async def reset_fail_count(self, route_id: int) -> None:
        """重置失败计数（不恢复降级状态，仅重置计数）"""
        status = await self.get_status(route_id)
        if status is None:
            return

        data = {
            "degraded_type": status.degraded_type.value,
            "fail_count": 0,
            "last_fail_time": time.time(),
        }

        cache_key = self._get_key(route_id)
        await self._cache._memory.set(cache_key, data, self.DEFAULT_TTL)

        if self._cache._redis and self._cache._redis.is_available:
            from llm_router.services.cache.serializer import CacheSerializer
            serializer = CacheSerializer()
            raw = serializer.serialize(data)
            await self._cache._redis.set(cache_key, raw, self.DEFAULT_TTL)

    def _from_dict(self, route_id: int, data: dict) -> RouteDegradedStatus:
        return RouteDegradedStatus(
            route_id=route_id,
            degraded_type=DegradedType(data["degraded_type"]),
            fail_count=data["fail_count"],
            last_fail_time=data["last_fail_time"],
        )
