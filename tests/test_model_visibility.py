from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from llm_router.core.database import Base
from llm_router.domain.models import ApiKey, LogicalModel
from llm_router.services.model_visibility import list_visible_logical_models


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session

    await engine.dispose()


async def _seed_models(session: AsyncSession) -> list[LogicalModel]:
    models = [
        LogicalModel(name="gpt-4o", is_active=True),
        LogicalModel(name="claude-sonnet", is_active=True),
        LogicalModel(name="disabled-model", is_active=False),
    ]
    session.add_all(models)
    await session.commit()
    for model in models:
        await session.refresh(model)
    return models


@pytest.mark.asyncio
async def test_list_visible_logical_models_returns_all_active_models_for_unrestricted_key(db_session: AsyncSession):
    await _seed_models(db_session)
    api_key = ApiKey(
        name="unrestricted",
        key_hash="hash-1",
        encrypted_key="enc",
        status="active",
        balance=Decimal("1"),
        allowed_logical_models_json=[],
    )

    visible_models = await list_visible_logical_models(db_session, api_key)

    assert [model.name for model in visible_models] == ["claude-sonnet", "gpt-4o"]


@pytest.mark.asyncio
async def test_list_visible_logical_models_supports_id_based_permissions(db_session: AsyncSession):
    models = await _seed_models(db_session)
    api_key = ApiKey(
        name="restricted-by-id",
        key_hash="hash-2",
        encrypted_key="enc",
        status="active",
        balance=Decimal("1"),
        allowed_logical_models_json=[models[1].id],
    )

    visible_models = await list_visible_logical_models(db_session, api_key)

    assert [model.name for model in visible_models] == ["claude-sonnet"]


@pytest.mark.asyncio
async def test_list_visible_logical_models_returns_empty_for_non_id_permissions(db_session: AsyncSession):
    _ = await _seed_models(db_session)
    api_key = ApiKey(
        name="invalid-permissions",
        key_hash="hash-3",
        encrypted_key="enc",
        status="active",
        balance=Decimal("1"),
        allowed_logical_models_json=["1"],
    )

    visible_models = await list_visible_logical_models(db_session, api_key)

    assert visible_models == []
