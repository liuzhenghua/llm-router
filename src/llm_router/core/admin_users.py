from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_router.core.security import hash_password, verify_password
from llm_router.domain.models import AdminUser


class AdminUserService:
    """DB-backed async admin user service."""

    async def has_any_user(self, session: AsyncSession) -> bool:
        result = await session.execute(select(func.count(AdminUser.id)))
        count = result.scalar()
        return (count or 0) > 0

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
        result = await session.execute(
            select(AdminUser).where(AdminUser.username == username, AdminUser.is_active == True)  # noqa: E712
        )
        user = result.scalar_one_or_none()
        if not user:
            return False
        return verify_password(password, user.password_hash)
