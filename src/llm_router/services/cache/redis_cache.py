from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as redis

logger = logging.getLogger(__name__)


class RedisCache:
    """
    Redis 缓存层（server 模式使用）

    特性：
    - 降级策略：Redis 不可用时自动跳过，不影响主流程
    - TTL 控制
    """

    KEY_PREFIX = "llm_router:cache:"

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        default_ttl: int = 60,
    ):
        self._client: redis.Redis | None = None
        self._config = {
            "host": host,
            "port": port,
            "db": db,
            "password": password,
            "decode_responses": True,
        }
        self._default_ttl = default_ttl
        self._available = False

    async def connect(self) -> None:
        """连接 Redis"""
        import redis.asyncio as redis

        try:
            self._client = redis.Redis(**self._config)
            await self._client.ping()
            self._available = True
            logger.info("Redis cache connected")
        except Exception as exc:
            logger.warning(f"Redis not available: {exc}. Cache disabled.")
            self._available = False
            self._client = None

    async def close(self) -> None:
        """关闭连接"""
        if self._client:
            await self._client.close()
            self._client = None
            self._available = False

    @property
    def is_available(self) -> bool:
        return self._available

    def _key(self, key: str) -> str:
        return f"{self.KEY_PREFIX}{key}"

    async def get(self, key: str) -> str | None:
        """获取缓存值"""
        if not self._available or not self._client:
            return None
        try:
            return await self._client.get(self._key(key))
        except Exception as exc:
            logger.warning(f"Redis get error: {exc}")
            return None

    async def set(self, key: str, value: str, ttl: int | None = None) -> bool:
        """设置缓存值"""
        if not self._available or not self._client:
            return False
        try:
            ttl = ttl if ttl is not None else self._default_ttl
            await self._client.setex(self._key(key), ttl, value)
            return True
        except Exception as exc:
            logger.warning(f"Redis set error: {exc}")
            return False

    async def delete(self, key: str) -> bool:
        """删除缓存"""
        if not self._available or not self._client:
            return False
        try:
            await self._client.delete(self._key(key))
            return True
        except Exception as exc:
            logger.warning(f"Redis delete error: {exc}")
            return False

    # ==================== ZSET 操作（用于增量队列）====================

    async def zadd(self, key: str, mapping: dict[str, float]) -> bool:
        """ZADD for spend queue"""
        if not self._available or not self._client:
            return False
        try:
            await self._client.zadd(self._key(key), mapping)
            return True
        except Exception as exc:
            logger.warning(f"Redis zadd error: {exc}")
            return False

    async def zrange(self, key: str, start: int, end: int) -> list[str]:
        """ZRANGE for spend queue"""
        if not self._available or not self._client:
            return []
        try:
            return await self._client.zrange(self._key(key), start, end)
        except Exception as exc:
            logger.warning(f"Redis zrange error: {exc}")
            return []

    async def zrem(self, key: str, *members: str) -> int:
        """ZREM for spend queue"""
        if not self._available or not self._client:
            return 0
        try:
            return await self._client.zrem(self._key(key), *members)
        except Exception as exc:
            logger.warning(f"Redis zrem error: {exc}")
            return 0

    async def zcard(self, key: str) -> int:
        """ZCARD for spend queue size"""
        if not self._available or not self._client:
            return 0
        try:
            return await self._client.zcard(self._key(key))
        except Exception as exc:
            logger.warning(f"Redis zcard error: {exc}")
            return 0

    # ==================== 分布式锁操作 ====================

    async def set_lock(self, key: str, value: str, ttl: int) -> bool:
        """SET NX EX for distributed lock"""
        if not self._available or not self._client:
            return False
        try:
            result = await self._client.set(self._key(key), value, nx=True, ex=ttl)
            return bool(result)
        except Exception as exc:
            logger.warning(f"Redis set_lock error: {exc}")
            return False

    async def eval_lock(self, key: str, script: str, num_keys: int, *args: str) -> bool:
        """Execute Lua script for lock release/extend"""
        if not self._available or not self._client:
            return False
        try:
            result = await self._client.eval(script, num_keys, self._key(key), *args)
            return bool(result)
        except Exception as exc:
            logger.warning(f"Redis eval error: {exc}")
            return False