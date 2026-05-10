from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from llm_router.core.database import Base
from llm_router.core.security import hash_api_key
from llm_router.domain.enums import ProviderProtocol
from llm_router.domain.models import ApiKey, LogicalModel
from llm_router.domain.schemas import CachedApiKey
from llm_router.services.cache.api_key_cache import ApiKeyCache
from llm_router.services.cache.core.dual_cache import DualCache
from llm_router.services.cache.core.in_memory_cache import InMemoryCache
from llm_router.services.cache.public_logical_model_cache import PublicLogicalModelCache
from llm_router.services.router import resolve_request_context
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
        LogicalModel(name="public-model", is_active=True, is_public=True),
        LogicalModel(name="disabled-model", is_active=False),
    ]
    session.add_all(models)
    await session.commit()
    for model in models:
        await session.refresh(model)
    return models


class _NoDbSession:
    async def execute(self, stmt):
        raise AssertionError(f"unexpected DB query: {stmt}")


class _FakeDualCache:
    def __init__(self, api_key_data: dict, public_models: list[dict]):
        self._api_key_data = api_key_data
        self._public_models = public_models

    async def get(self, key: str, **kwargs):
        if key.startswith("apikey:hash:"):
            return self._api_key_data
        assert key == "logical:public"
        return self._public_models


def _set_test_visibility_caches(monkeypatch: pytest.MonkeyPatch, dual_cache) -> None:
    monkeypatch.setattr(
        "llm_router.services.cache.api_key_cache.api_key_cache",
        ApiKeyCache(dual_cache),
    )
    monkeypatch.setattr(
        "llm_router.services.cache.public_logical_model_cache.public_logical_model_cache",
        PublicLogicalModelCache(dual_cache),
    )


def _set_empty_visibility_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    dual_cache = DualCache(
        settings=object(),
        in_memory_cache=InMemoryCache(),
        redis_cache=None,
    )
    _set_test_visibility_caches(monkeypatch, dual_cache)


@pytest.mark.asyncio
async def test_list_visible_logical_models_returns_public_models_when_no_models_are_assigned(db_session: AsyncSession):
    await _seed_models(db_session)
    api_key = ApiKey(
        name="public-only",
        key_hash="hash-1",
        encrypted_key="enc",
        status="active",
        balance=Decimal("1"),
        allowed_logical_models_json=[],
    )

    visible_models = await list_visible_logical_models(db_session, api_key)

    assert [model.name for model in visible_models] == ["public-model"]


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

    assert [model.name for model in visible_models] == ["claude-sonnet", "public-model"]


@pytest.mark.asyncio
async def test_list_visible_logical_models_returns_public_models_for_non_id_permissions(db_session: AsyncSession):
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

    assert [model.name for model in visible_models] == ["public-model"]


@pytest.mark.asyncio
async def test_resolve_request_context_allows_public_model_for_restricted_key(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    models = await _seed_models(db_session)
    raw_key = "sk-test-public"
    api_key = ApiKey(
        name="restricted",
        key_hash=hash_api_key(raw_key),
        encrypted_key="enc",
        status="active",
        balance=Decimal("1"),
        allowed_logical_models_json=[models[1].id],
    )
    db_session.add(api_key)
    await db_session.commit()
    _set_empty_visibility_caches(monkeypatch)

    _, context = await resolve_request_context(
        db_session,
        raw_api_key=raw_key,
        logical_model_name="public-model",
        protocol=ProviderProtocol.OPENAI,
        payload={"model": "public-model"},
        stream=False,
        headers={},
    )

    assert context.logical_model_name == "public-model"


@pytest.mark.asyncio
async def test_resolve_request_context_rejects_unassigned_private_model(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    await _seed_models(db_session)
    raw_key = "sk-test-private"
    api_key = ApiKey(
        name="public-only",
        key_hash=hash_api_key(raw_key),
        encrypted_key="enc",
        status="active",
        balance=Decimal("1"),
        allowed_logical_models_json=[],
    )
    db_session.add(api_key)
    await db_session.commit()
    _set_empty_visibility_caches(monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        await resolve_request_context(
            db_session,
            raw_api_key=raw_key,
            logical_model_name="gpt-4o",
            protocol=ProviderProtocol.OPENAI,
            payload={"model": "gpt-4o"},
            stream=False,
            headers={},
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_resolve_request_context_uses_cached_public_models_without_db(
    monkeypatch: pytest.MonkeyPatch,
):
    raw_key = "sk-test-cached-public"
    api_key_data = CachedApiKey(
        id=1,
        name="cached-key",
        status="active",
        balance=Decimal("1"),
        daily_budget_limit=None,
        daily_spend_amount=Decimal("0"),
        daily_spend_date=None,
        qps_limit=5,
        allowed_logical_models=[],
    ).to_dict()
    dual_cache = _FakeDualCache(api_key_data, [{"id": 3, "name": "public-model"}])

    async def _noop_check(api_key_id: int, qps_limit: int) -> None:
        return None

    _set_test_visibility_caches(monkeypatch, dual_cache)
    monkeypatch.setattr("llm_router.services.router.rate_limiter.check", _noop_check)

    _, context = await resolve_request_context(
        _NoDbSession(),
        raw_api_key=raw_key,
        logical_model_name="public-model",
        protocol=ProviderProtocol.OPENAI,
        payload={"model": "public-model"},
        stream=False,
        headers={},
    )

    assert context.logical_model_ids == [3]
