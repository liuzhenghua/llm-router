from __future__ import annotations

from sqlalchemy import func, inspect, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.schema import CreateTable, CreateIndex

from llm_router.core.security import hash_password, verify_password
from llm_router.domain.models import AdminUser


class AdminUserService:
    """DB-backed async admin user service."""

    async def is_table_missing(self) -> bool:
        """Check if the admin_users table exists using a dedicated engine connection."""
        from llm_router.core.database import engine

        async with engine.connect() as conn:
            has_table = await conn.run_sync(
                lambda c: inspect(c).has_table(AdminUser.__tablename__)
            )
        return not has_table

    async def has_any_user(self, session: AsyncSession) -> bool:
        """Returns False if no users exist or the table is missing."""
        try:
            result = await session.execute(select(func.count(AdminUser.id)))
            return (result.scalar() or 0) > 0
        except (OperationalError, ProgrammingError):
            await session.rollback()
            return False

    async def create_or_update_user(
        self,
        session: AsyncSession,
        username: str,
        password: str,
        is_active: bool = True,
    ) -> None:
        result = await session.execute(select(AdminUser).where(AdminUser.username == username))
        user = result.scalar_one_or_none()
        password_hash = hash_password(password)
        if user:
            user.password_hash = password_hash
            user.is_active = is_active
        else:
            user = AdminUser(username=username, password_hash=password_hash, is_active=is_active)
            session.add(user)
        await session.commit()

    async def authenticate(self, session: AsyncSession, username: str, password: str) -> bool:
        try:
            result = await session.execute(
                select(AdminUser).where(AdminUser.username == username, AdminUser.is_active == True)  # noqa: E712
            )
            user = result.scalar_one_or_none()
            if not user:
                return False
            return verify_password(password, user.password_hash)
        except (OperationalError, ProgrammingError):
            await session.rollback()
            return False

    def get_full_schema_sql(self, use_mysql: bool = False) -> str:
        """Return the CREATE TABLE DDL for every table in the schema (sorted by dependency)."""
        from llm_router.core.database import Base
        import llm_router.domain.models  # noqa: F401 — ensure all models are registered

        if use_mysql:
            from sqlalchemy.dialects.mysql import dialect as _dialect
        else:
            from sqlalchemy.dialects.sqlite import dialect as _dialect

        d = _dialect()
        parts: list[str] = []
        for table in Base.metadata.sorted_tables:
            ddl = str(CreateTable(table).compile(dialect=d)).strip()
            parts.append(ddl + ";")
            # CreateTable only emits PRIMARY KEY and UNIQUE inline; emit
            # separate CREATE INDEX statements for every non-unique index.
            for index in sorted(table.indexes, key=lambda i: i.name or ""):
                if not index.unique:
                    idx_ddl = str(CreateIndex(index).compile(dialect=d)).strip()
                    parts.append(idx_ddl + ";")
        return "\n\n".join(parts)
