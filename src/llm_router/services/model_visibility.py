from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_router.domain.models import ApiKey, LogicalModel


def build_visible_logical_models_stmt(api_key: ApiKey):
    stmt = select(LogicalModel).where(LogicalModel.is_active)
    raw_allowed_ids = api_key.allowed_logical_models_json or []
    if not raw_allowed_ids or not all(isinstance(model_id, int) for model_id in raw_allowed_ids):
        return stmt.where(LogicalModel.is_public).order_by(LogicalModel.name.asc(), LogicalModel.id.asc())

    allowed_ids = set(raw_allowed_ids)
    return stmt.where(or_(LogicalModel.id.in_(allowed_ids), LogicalModel.is_public)).order_by(
        LogicalModel.name.asc(),
        LogicalModel.id.asc(),
    )


async def list_visible_logical_models(session: AsyncSession, api_key: ApiKey) -> list[LogicalModel]:
    result = await session.execute(build_visible_logical_models_stmt(api_key))
    return result.scalars().all()
