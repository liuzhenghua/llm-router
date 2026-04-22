from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from llm_router.core.database import SessionLocal
from llm_router.domain.models import ApiKey
from llm_router.services.cache.redis_lock import RedisLockManager
from llm_router.services.cache.spend_queue import SpendDelta, SpendDeltaQueue

if TYPE_CHECKING:
    import redis.asyncio as redis

logger = logging.getLogger(__name__)

LOCK_KEY = "db_writer"
LOCK_TTL = 30  # 锁超时 30s
BATCH_SIZE = 100  # 每次最多处理 100 条


class DbSpendWriter:
    """
    DB 消费写入器：定期从增量队列消费并写入 DB

    设计要点：
    1. redis 模式：使用 Redis 分布式锁，确保只有一个 Pod 执行
    2. 本地模式：直接定时写入
    3. 使用 SQLAlchemy UPDATE ... SET balance = balance + delta（原子操作）
    """

    def __init__(
        self,
        spend_queue: SpendDeltaQueue,
        redis_client: redis.Redis | None = None,
        redis_enabled: bool = False,
        flush_interval: int = 30,
    ):
        self._queue = spend_queue
        self._redis = redis_client
        self._redis_enabled = redis_enabled
        self._flush_interval = flush_interval
        self._lock_manager = RedisLockManager(redis_client) if redis_client else None
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """启动后台刷新任务"""
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        logger.info("DbSpendWriter started")

    async def stop(self) -> None:
        """停止后台刷新任务"""
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("DbSpendWriter stopped")

    async def _run_loop(self) -> None:
        """主循环"""
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(self._flush_interval)
                await self._flush()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Flush loop error: {exc}", exc_info=True)

    async def _flush(self) -> None:
        """执行一次刷新"""
        # redis 模式：先抢锁
        if self._redis_enabled and self._lock_manager:
            acquired = await self._lock_manager.acquire(LOCK_KEY, ttl=LOCK_TTL)
            if not acquired:
                logger.debug("Lock not acquired, skipping this flush cycle")
                return
            holder_id = self._lock_manager.get_holder_id()
            logger.debug(f"Acquired db_writer lock, holder: {holder_id}")

        try:
            # 消费所有待处理的增量
            total_processed = 0
            while True:
                deltas = await self._queue.pop_batch(BATCH_SIZE)
                if not deltas:
                    break

                await self._write_to_db(deltas)
                total_processed += len(deltas)

            if total_processed > 0:
                logger.info(f"Flushed {total_processed} spend deltas to DB")

        finally:
            if self._redis_enabled and self._lock_manager:
                await self._lock_manager.release(LOCK_KEY)
                logger.debug("Released db_writer lock")

    async def _write_to_db(self, deltas: list[SpendDelta]) -> None:
        """
        批量写入 DB：使用 UPDATE ... SET balance = balance + delta

        这种方式在多 Pod 同时写入时是安全的（spend += delta）
        """
        if not deltas:
            return

        # 按 api_key_id 聚合
        aggregated: dict[int, Decimal] = defaultdict(Decimal)
        for delta in deltas:
            aggregated[delta.api_key_id] += delta.delta_amount

        async with SessionLocal() as session:
            for api_key_id, total_delta in aggregated.items():
                # 使用 SQLAlchemy Core 原生 SQL 执行 increment
                # UPDATE api_keys
                # SET balance = balance + :delta,
                #     daily_spend_amount = daily_spend_amount + :delta,
                #     updated_at = NOW()
                # WHERE id = :api_key_id

                stmt = (
                    update(ApiKey)
                    .where(ApiKey.id == api_key_id)
                    .values(
                        balance=ApiKey.balance + total_delta,
                        daily_spend_amount=ApiKey.daily_spend_amount + total_delta,
                        updated_at=func.now(),
                    )
                )
                await session.execute(stmt)

            await session.commit()


# 全局实例
db_writer: DbSpendWriter | None = None


def get_db_writer() -> DbSpendWriter | None:
    return db_writer


def set_db_writer(writer: DbSpendWriter) -> None:
    global db_writer
    db_writer = writer