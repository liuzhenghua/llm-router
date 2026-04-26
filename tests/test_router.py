from decimal import Decimal

import pytest

from llm_router.core.security import Encryptor
from llm_router.domain.enums import ProviderProtocol
from llm_router.domain.schemas import CachedProvider, CachedRoute
from llm_router.services.router import resolve_provider_candidates


class _FakeDualCache:
    def __init__(self, routes_by_model: dict[int, list[dict]], providers_by_id: dict[int, dict]):
        self._routes_by_model = routes_by_model
        self._providers_by_id = providers_by_id
        self._memory = _NullMemory()
        self._redis = None

    async def get_routes_by_logical_model(self, logical_model_id: int) -> list[dict] | None:
        return self._routes_by_model.get(logical_model_id)

    async def get_provider(self, provider_id: int) -> dict | None:
        return self._providers_by_id.get(provider_id)


class _UnusedSession:
    async def execute(self, stmt):  # pragma: no cover - this path should not be hit in the test
        raise AssertionError(f"unexpected DB query: {stmt}")


class _NullMemory:
    async def get(self, key: str):
        return None


@pytest.mark.asyncio
async def test_same_name_logical_models_keep_distinct_routes_for_same_provider(monkeypatch: pytest.MonkeyPatch):
    provider_id = 101
    encryptor = Encryptor("test-secret")
    provider_data = CachedProvider(
        id=provider_id,
        name="shared-provider",
        description=None,
        openai_endpoint="https://example.com/v1",
        anthropic_endpoint=None,
        encrypted_api_key=encryptor.encrypt("secret"),
        upstream_model_name="gpt-4o",
        input_token_price=Decimal("1"),
        output_token_price=Decimal("2"),
        cache_read_token_price=Decimal("0"),
        cache_write_token_price=Decimal("0"),
        supports_prompt_cache=False,
        timeout_seconds=30,
        is_active=True,
    ).to_dict()

    routes_by_model = {
        1: [
            CachedRoute(
                route_id=11,
                logical_model_id=1,
                provider_model_id=provider_id,
                priority=10,
                weight=1,
                is_fallback=False,
                status="active",
            ).to_dict()
        ],
        2: [
            CachedRoute(
                route_id=22,
                logical_model_id=2,
                provider_model_id=provider_id,
                priority=10,
                weight=3,
                is_fallback=False,
                status="active",
            ).to_dict()
        ],
    }

    monkeypatch.setattr(
        "llm_router.services.router.get_dual_cache",
        lambda: _FakeDualCache(routes_by_model, {provider_id: provider_data}),
    )
    monkeypatch.setattr("llm_router.services.router.encryptor", encryptor)

    groups = await resolve_provider_candidates(_UnusedSession(), [1, 2], ProviderProtocol.OPENAI)

    assert len(groups) == 1
    assert groups[0].priority == 10
    assert [provider.route_id for provider in groups[0].providers] == [11, 22]
    assert [provider.logical_model_id for provider in groups[0].providers] == [1, 2]
    assert [provider.weight for provider in groups[0].providers] == [1, 3]
