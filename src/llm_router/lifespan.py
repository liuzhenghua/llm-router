from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from llm_router.core.config import get_settings
from llm_router.core.database import SessionLocal, init_db
from llm_router.services.cache.db_writer import DbSpendWriter, set_db_writer
from llm_router.services.cache.dual_cache import DualCache, set_dual_cache
from llm_router.services.cache.in_memory_cache import InMemoryCache
from llm_router.services.cache.redis_cache import RedisCache
from llm_router.services.cache.redis_lock import RedisLockManager, set_lock_manager
from llm_router.services.cache.spend_queue import SpendDeltaQueue, set_spend_queue
from llm_router.services.degraded_route_recovery import run_recovery_task

logger = logging.getLogger(__name__)
settings = get_settings()

# Global cache components
_dual_cache: DualCache | None = None
_spend_queue: SpendDeltaQueue | None = None
_db_writer: DbSpendWriter | None = None
_redis_cache: RedisCache | None = None
_degraded_recovery_task: asyncio.Task | None = None

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _dual_cache, _spend_queue, _db_writer, _redis_cache

    try:
        await init_db()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "init_db() failed — tables may already exist or the DB user lacks DDL privileges. "
            "Continuing startup. Error: %s",
            exc,
        )

    # Initialize cache components
    in_memory_cache = InMemoryCache(
        max_size=10000,
        default_ttl=settings.default_in_memory_ttl,
    )

    redis_client = None
    if settings.redis_enabled:
        _redis_cache = RedisCache(
            url=settings.redis_url,
            password=settings.redis_password,
            default_ttl=settings.default_redis_ttl,
        )
        await _redis_cache.connect()
        redis_client = _redis_cache._client

    _dual_cache = DualCache(
        settings=settings,
        in_memory_cache=in_memory_cache,
        redis_cache=_redis_cache,
        in_memory_ttl=settings.default_in_memory_ttl,
        redis_ttl=settings.default_redis_ttl,
    )
    set_dual_cache(_dual_cache)

    _spend_queue = SpendDeltaQueue(
        redis_enabled=settings.redis_enabled,
        redis_client=redis_client,
    )
    set_spend_queue(_spend_queue)

    if settings.redis_enabled and redis_client:
        lock_manager = RedisLockManager(redis_client)
        set_lock_manager(lock_manager)

    _db_writer = DbSpendWriter(
        spend_queue=_spend_queue,
        redis_client=redis_client,
        redis_enabled=settings.redis_enabled,
        flush_interval=settings.spend_queue_flush_interval,
    )
    set_db_writer(_db_writer)
    await _db_writer.start()

    global _degraded_recovery_task
    _degraded_recovery_task = asyncio.create_task(
        run_recovery_task(SessionLocal, interval_seconds=300)
    )
    logger.info("Degraded route recovery task started")
    logger.info(
        "Cache system initialized (redis_enabled=%s, use_mysql=%s)",
        settings.redis_enabled,
        settings.use_mysql,
    )

    yield

    # Shutdown
    if _degraded_recovery_task:
        _degraded_recovery_task.cancel()
        try:
            await _degraded_recovery_task
        except asyncio.CancelledError:
            pass
        logger.info("Degraded route recovery task stopped")
    if _db_writer:
        await _db_writer.stop()
    if _redis_cache:
        await _redis_cache.close()
    logger.info("Cache system shut down")
