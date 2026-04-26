from __future__ import annotations

from sqlalchemy import false, select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_router.domain.models import ApiKey, LogicalModel


def build_visible_logical_models_stmt(api_key: ApiKey):
    stmt = select(LogicalModel).where(LogicalModel.is_active)
    allowed_ids = api_key.allowed_logical_models_json or []
    if not allowed_ids:
        return stmt.order_by(LogicalModel.name.asc(), LogicalModel.id.asc())

    if not all(isinstance(model_id, int) for model_id in allowed_ids):
        return stmt.where(false()).order_by(LogicalModel.name.asc(), LogicalModel.id.asc())

    return stmt.where(LogicalModel.id.in_(allowed_ids)).order_by(LogicalModel.name.asc(), LogicalModel.id.asc())


async def list_visible_logical_models(session: AsyncSession, api_key: ApiKey) -> list[LogicalModel]:
    result = await session.execute(build_visible_logical_models_stmt(api_key))
    return result.scalars().all()
