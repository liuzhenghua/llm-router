from decimal import Decimal

import pytest

from llm_router.core.security import Encryptor
from llm_router.domain.enums import ProviderProtocol
from llm_router.domain.models import ProviderModel
from llm_router.domain.schemas import CachedProvider, CachedRoute
from llm_router.services.cache.degraded_cache import DegradedRouteCache
from llm_router.services.cache.provider_cache import ProviderCache
from llm_router.services.cache.route_cache import RouteCache
from llm_router.services.router import resolve_provider_candidates


class _FakeDualCache:
    def __init__(self, routes_by_model: dict[int, list[dict]], providers_by_id: dict[int, dict]):
        self._routes_by_model = routes_by_model
        self._providers_by_id = providers_by_id
        self._store = {}
        self._memory = _NullMemory()
        self._redis = None

    async def get(self, key: str, **kwargs):
        if key.startswith("route:logical:"):
            return self._routes_by_model.get(int(key.rsplit(":", 1)[1]))
        if key.startswith("provider:id:"):
            return self._providers_by_id.get(int(key.rsplit(":", 1)[1]))
        return self._store.get(key)

    async def set(self, key: str, value, **kwargs):
        if key.startswith("provider:id:"):
            self._providers_by_id[int(key.rsplit(":", 1)[1])] = value
        else:
            self._store[key] = value


class _UnusedSession:
    async def execute(self, stmt):  # pragma: no cover - this path should not be hit in the test
        raise AssertionError(f"unexpected DB query: {stmt}")

    async def get(self, model, ident):  # pragma: no cover - this path should not be hit in the test
        raise AssertionError(f"unexpected DB get: {model}, {ident}")


class _ProviderSession:
    def __init__(self, provider: ProviderModel):
        self.provider = provider

    async def get(self, model, ident):
        assert model is ProviderModel
        assert ident == self.provider.id
        return self.provider


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

    dual_cache = _FakeDualCache(routes_by_model, {provider_id: provider_data})
    monkeypatch.setattr("llm_router.services.cache.degraded_cache.degraded_route_cache", DegradedRouteCache(dual_cache))
    monkeypatch.setattr("llm_router.services.cache.route_cache.route_cache", RouteCache(dual_cache))
    monkeypatch.setattr("llm_router.services.cache.provider_cache.provider_cache", ProviderCache(dual_cache))
    monkeypatch.setattr("llm_router.services.router.encryptor", encryptor)

    groups = await resolve_provider_candidates(_UnusedSession(), [1, 2], ProviderProtocol.OPENAI)

    assert len(groups) == 1
    assert groups[0].priority == 10
    assert [provider.route_id for provider in groups[0].providers] == [11, 22]
    assert [provider.logical_model_id for provider in groups[0].providers] == [1, 2]
    assert [provider.weight for provider in groups[0].providers] == [1, 3]


@pytest.mark.asyncio
async def test_provider_cache_miss_backfills_from_db_for_cached_route(monkeypatch: pytest.MonkeyPatch):
    provider_id = 101
    encryptor = Encryptor("test-secret")
    provider_model = ProviderModel(
        id=provider_id,
        name="fresh-provider",
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
        deleted_at=None,
    )
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
        ]
    }

    dual_cache = _FakeDualCache(routes_by_model, {})
    monkeypatch.setattr("llm_router.services.cache.degraded_cache.degraded_route_cache", DegradedRouteCache(dual_cache))
    monkeypatch.setattr("llm_router.services.cache.route_cache.route_cache", RouteCache(dual_cache))
    monkeypatch.setattr("llm_router.services.cache.provider_cache.provider_cache", ProviderCache(dual_cache))
    monkeypatch.setattr("llm_router.services.router.encryptor", encryptor)

    groups = await resolve_provider_candidates(_ProviderSession(provider_model), [1], ProviderProtocol.OPENAI)

    assert len(groups) == 1
    assert groups[0].providers[0].provider.name == "fresh-provider"
    assert dual_cache._providers_by_id[provider_id]["name"] == "fresh-provider"


@pytest.mark.asyncio
async def test_pending_fail_count_does_not_filter_route(monkeypatch: pytest.MonkeyPatch):
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
        ]
    }

    dual_cache = _FakeDualCache(routes_by_model, {provider_id: provider_data})
    degraded_cache = DegradedRouteCache(dual_cache)
    await degraded_cache.increment_fail_count(11)
    monkeypatch.setattr("llm_router.services.cache.degraded_cache.degraded_route_cache", degraded_cache)
    monkeypatch.setattr("llm_router.services.cache.route_cache.route_cache", RouteCache(dual_cache))
    monkeypatch.setattr("llm_router.services.cache.provider_cache.provider_cache", ProviderCache(dual_cache))
    monkeypatch.setattr("llm_router.services.router.encryptor", encryptor)

    groups = await resolve_provider_candidates(_UnusedSession(), [1], ProviderProtocol.OPENAI)

    assert len(groups) == 1
    assert groups[0].providers[0].route_id == 11
