from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as redis

logger = logging.getLogger(__name__)

# Redis key prefix for locks
LOCK_PREFIX = "llm_router:lock:"


class RedisLockManager:
    """
    Redis 分布式锁管理器

    使用 SET NX EX 实现基本的分布式锁
    锁的 key 命名约定：llm_router:lock:{lock_name}
    """

    def __init__(self, redis_client: redis.Redis | None):
        self._redis = redis_client
        self._holder_id = str(uuid.uuid4())  # 当前进程的唯一标识

    def get_holder_id(self) -> str:
        return self._holder_id

    async def acquire(self, lock_name: str, ttl: int = 30, retry_times: int = 0, retry_delay: float = 0.1) -> bool:
        """
        尝试获取锁

        Args:
            lock_name: 锁名称（不含前缀）
            ttl: 锁过期时间（秒）
            retry_times: 重试次数
            retry_delay: 重试间隔

        Returns:
            是否成功获取锁
        """
        if not self._redis:
            return False

        key = f"{LOCK_PREFIX}{lock_name}"

        for attempt in range(retry_times + 1):
            # SET key value NX EX ttl
            acquired = await self._redis.set(
                key,
                self._holder_id,
                nx=True,
                ex=ttl,
            )
            if acquired:
                logger.debug(f"Lock acquired: {lock_name}, holder: {self._holder_id}")
                return True

            if attempt < retry_times:
                await asyncio.sleep(retry_delay)

        logger.debug(f"Lock not acquired: {lock_name}")
        return False

    async def release(self, lock_name: str) -> bool:
        """
        释放锁（仅持有者可以释放）

        使用 Lua 脚本保证原子性：
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        if not self._redis:
            return False

        key = f"{LOCK_PREFIX}{lock_name}"
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        try:
            result = await self._redis.eval(lua_script, 1, key, self._holder_id)
            released = bool(result)
            if released:
                logger.debug(f"Lock released: {lock_name}")
            else:
                logger.debug(f"Lock release failed (not holder): {lock_name}")
            return released
        except Exception as exc:
            logger.warning(f"Failed to release lock {lock_name}: {exc}")
            return False

    async def extend(self, lock_name: str, additional_ttl: int) -> bool:
        """
        延长锁的过期时间（仅持有者可操作）
        """
        if not self._redis:
            return False

        key = f"{LOCK_PREFIX}{lock_name}"
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("expire", KEYS[1], ARGV[2])
        else
            return 0
        end
        """
        try:
            result = await self._redis.eval(lua_script, 1, key, self._holder_id, additional_ttl)
            return bool(result)
        except Exception as exc:
            logger.warning(f"Failed to extend lock {lock_name}: {exc}")
            return False


# 全局实例
lock_manager: RedisLockManager | None = None


def get_lock_manager() -> RedisLockManager | None:
    return lock_manager


def set_lock_manager(manager: RedisLockManager) -> None:
    global lock_manager
    lock_manager = manager