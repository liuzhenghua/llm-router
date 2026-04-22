from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import jinja2
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.middleware.sessions import SessionMiddleware

from llm_router.api import admin, anthropic, openai
from llm_router.core.admin_users import AdminUserStore
from llm_router.core.config import get_settings
from llm_router.core.database import SessionLocal, init_db
from llm_router.services.cache.db_writer import DbSpendWriter, set_db_writer
from llm_router.services.cache.dual_cache import DualCache, set_dual_cache
from llm_router.services.cache.in_memory_cache import InMemoryCache
from llm_router.services.cache.redis_cache import RedisCache
from llm_router.services.cache.redis_lock import set_lock_manager, RedisLockManager
from llm_router.services.cache.spend_queue import SpendDeltaQueue, set_spend_queue
from llm_router.services.degraded_route_recovery import run_recovery_task

logger = logging.getLogger(__name__)

BASE_PATH = Path(__file__).resolve().parent
settings = get_settings()

# Global cache components
_dual_cache: DualCache | None = None
_spend_queue: SpendDeltaQueue | None = None
_db_writer: DbSpendWriter | None = None
_redis_cache: RedisCache | None = None
_degraded_recovery_task: asyncio.Task | None = None


def _format_decimal(value: Decimal | float | str | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        value = Decimal(value)
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return str(value.to_integral_value())
        return f"{value:f}".rstrip("0").rstrip(".")
    return str(value)


def _format_datetime(value: str | None) -> str:
    """Return UTC ISO string with Z suffix; frontend JS converts to local timezone."""
    if value is None:
        return ""
    try:
        dt = datetime.fromisoformat(value)
        # stored as naive UTC, append Z to indicate UTC
        return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    except (ValueError, TypeError):
        return value


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _dual_cache, _spend_queue, _db_writer, _redis_cache

    await init_db()

    # === 初始化缓存组件 ===
    in_memory_cache = InMemoryCache(
        max_size=10000,
        default_ttl=settings.default_in_memory_ttl,
    )

    redis_client = None
    if settings.redis_enabled:
        # Redis 模式：初始化 Redis 缓存
        _redis_cache = RedisCache(
            url=settings.redis_url,
            password=settings.redis_password,
            default_ttl=settings.default_redis_ttl,
        )
        await _redis_cache.connect()
        redis_client = _redis_cache._client  # 用于 spend_queue 和 lock_manager

    # 初始化 DualCache
    _dual_cache = DualCache(
        settings=settings,
        in_memory_cache=in_memory_cache,
        redis_cache=_redis_cache,
        in_memory_ttl=settings.default_in_memory_ttl,
        redis_ttl=settings.default_redis_ttl,
    )
    set_dual_cache(_dual_cache)

    # 初始化增量队列
    _spend_queue = SpendDeltaQueue(
        redis_enabled=settings.redis_enabled,
        redis_client=redis_client,
    )
    set_spend_queue(_spend_queue)

    # 初始化 Redis 锁管理器
    if settings.redis_enabled and redis_client:
        lock_manager = RedisLockManager(redis_client)
        set_lock_manager(lock_manager)

    # 初始化 DB 写入器
    _db_writer = DbSpendWriter(
        spend_queue=_spend_queue,
        redis_client=redis_client,
        redis_enabled=settings.redis_enabled,
        flush_interval=settings.spend_queue_flush_interval,
    )
    set_db_writer(_db_writer)
    await _db_writer.start()

    # 启动降级路由恢复定时任务
    global _degraded_recovery_task
    _degraded_recovery_task = asyncio.create_task(
        run_recovery_task(SessionLocal, interval_seconds=300)  # 5 分钟
    )
    logger.info("Degraded route recovery task started")

    logger.info(f"Cache system initialized (redis_enabled={settings.redis_enabled}, use_mysql={settings.use_mysql})")

    yield

    # === 清理 ===
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


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
    app.mount("/static", StaticFiles(directory=BASE_PATH / "static"), name="static")
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(BASE_PATH / "templates")),
    )
    env.filters["format_decimal"] = _format_decimal
    env.filters["format_datetime"] = _format_datetime
    app.state.templates = Jinja2Templates(env=env)
    app.state.admin_user_store = AdminUserStore(settings.admin_users_file)

    @app.middleware("http")
    async def db_session_middleware(request: Request, call_next):
        session = SessionLocal()
        request.state.db = session
        response = await call_next(request)
        background = response.background

        async def close_session() -> None:
            await session.close()
            if background is not None:
                await background()

        response.background = BackgroundTask(close_session)
        return response


    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "redis_enabled": settings.redis_enabled, "use_mysql": settings.use_mysql}

    @app.get("/")
    async def root():
        return RedirectResponse("/admin", status_code=303)

    app.include_router(openai.router)
    app.include_router(anthropic.router)
    app.include_router(admin.public_router, prefix="/admin")
    app.include_router(admin.protected_router, prefix="/admin")
    return app


app = create_app()


def main() -> None:
    uvicorn.run(
        "llm_router.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        factory=False,
        http="h11",
    )


if __name__ == "__main__":
    main()
