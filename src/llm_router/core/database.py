from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from llm_router.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()

mysql_connect_args = {
    "connect_timeout": 30,   # Only affects TCP handshake + MySQL greeting phase
} if settings.use_mysql else {}

engine = create_async_engine(
    settings.effective_database_url,
    echo=settings.debug,
    future=True,
    # Emit a lightweight SELECT 1 before handing a pooled connection to the caller.
    # This transparently recycles any connection that MySQL closed server-side
    # (e.g. after wait_timeout expires), preventing CR_SERVER_LOST (2013) errors.
    pool_pre_ping=True,
    # Recycle connections after 10 minutes to stay well below MySQL's default
    # wait_timeout (8 h). Only meaningful for MySQL; SQLite ignores it.
    pool_recycle=600,
    pool_size=10,
    max_overflow=20,
    connect_args=mysql_connect_args,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    from llm_router.domain import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def table_name(name: str) -> str:
    """Return the table name with the configured prefix applied."""
    return f"{settings.table_prefix}{name}"


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
