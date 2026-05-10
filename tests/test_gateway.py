from decimal import Decimal

import pytest
from fastapi import status

from llm_router.domain.enums import ProviderProtocol
from llm_router.domain.schemas import RequestContext, RoutableProvider, RoutableProviderGroup, RoutedProvider
from llm_router.services.cache.degraded_cache import DegradedType
from llm_router.services import gateway as gateway_module
from llm_router.services.gateway import _degraded_type_for_status, _protocol_error_response, _select_group_provider


def _make_routable_provider(route_id: int, *, weight: int = 1) -> RoutableProvider:
    return RoutableProvider(
        route_id=route_id,
        logical_model_id=route_id,
        provider=RoutedProvider(
            id=route_id,
            name=f"provider-{route_id}",
            protocol=ProviderProtocol.OPENAI,
            upstream_protocol=ProviderProtocol.OPENAI,
            endpoint="https://example.com/v1",
            api_key="secret",
            upstream_model_name="gpt-4o",
            timeout_seconds=30,
            input_token_price=Decimal("1"),
            output_token_price=Decimal("1"),
            cache_read_token_price=Decimal("0"),
            cache_write_token_price=Decimal("0"),
            supports_prompt_cache=False,
        ),
        weight=weight,
    )


def test_degraded_type_for_status_separates_auth_failures():
    assert _degraded_type_for_status(status.HTTP_401_UNAUTHORIZED) == DegradedType.AUTH_FAILED
    assert _degraded_type_for_status(status.HTTP_402_PAYMENT_REQUIRED) == DegradedType.QUOTA_EXHAUSTED
    assert _degraded_type_for_status(status.HTTP_403_FORBIDDEN) == DegradedType.QUOTA_EXHAUSTED
    # 429: 速率限制，与 5xx 一样通过连续失败计数降级，不在立即降级逻辑中处理
    assert _degraded_type_for_status(status.HTTP_429_TOO_MANY_REQUESTS) is None
    assert _degraded_type_for_status(status.HTTP_500_INTERNAL_SERVER_ERROR) is None


def test_select_group_provider_skips_routes_already_failed_in_current_request():
    providers = [
        _make_routable_provider(1),
        _make_routable_provider(2),
        _make_routable_provider(3),
    ]

    selected = _select_group_provider(providers, {1, 2})

    assert selected is not None
    assert selected.route_id == 3


def test_select_group_provider_returns_none_when_all_routes_were_already_attempted():
    providers = [
        _make_routable_provider(1),
        _make_routable_provider(2),
    ]

    selected = _select_group_provider(providers, {1, 2})

    assert selected is None


def test_protocol_error_response_uses_openai_error_shape():
    response = _protocol_error_response(
        ProviderProtocol.OPENAI,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "Internal server error",
    )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.body == (
        b'{"error":{"message":"Internal server error","type":"server_error","code":"server_error"}}'
    )


def test_protocol_error_response_uses_anthropic_error_shape():
    response = _protocol_error_response(
        ProviderProtocol.ANTHROPIC,
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "Internal server error",
    )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.body == (
        b'{"type":"error","error":{"type":"api_error","message":"Internal server error"}}'
    )


@pytest.mark.asyncio
async def test_handle_proxy_request_unexpected_exception_does_not_degrade_route(monkeypatch: pytest.MonkeyPatch):
    provider = _make_routable_provider(1)

    async def fake_resolve_request_context(*args, **kwargs):
        return object(), RequestContext(
            request_id="",
            protocol=ProviderProtocol.OPENAI,
            logical_model_name="gpt-4o",
            payload={"model": "gpt-4o"},
            stream=False,
            request_logging_enabled=False,
            response_logging_enabled=False,
            api_key_id=1,
            api_key_name="test-key",
            api_key_timezone="UTC",
            logical_model_id=1,
            logical_model_ids=[1],
        )

    async def fake_resolve_provider_candidates(*args, **kwargs):
        return [RoutableProviderGroup(priority=0, is_fallback=False, providers=[provider])]

    class FakeSession:
        async def close(self):
            return None

    class FakeHandler:
        async def proxy(self, *args, **kwargs):
            raise RuntimeError("gateway bug")

    class FakeDegradedCache:
        async def recover(self, route_id: int):
            raise AssertionError("recover should not be called")

        async def increment_fail_count(self, route_id: int):
            raise AssertionError("unexpected exceptions should not affect route fail count")

        async def mark_degraded(self, *args, **kwargs):
            raise AssertionError("unexpected exceptions should not degrade route")

    monkeypatch.setattr(gateway_module, "resolve_request_context", fake_resolve_request_context)
    monkeypatch.setattr(gateway_module, "resolve_provider_candidates", fake_resolve_provider_candidates)
    monkeypatch.setattr(gateway_module, "get_degraded_route_cache", lambda: FakeDegradedCache())
    monkeypatch.setattr(gateway_module, "OpenAINonStreamHandler", FakeHandler)

    response = await gateway_module.handle_proxy_request(
        FakeSession(),
        protocol=ProviderProtocol.OPENAI,
        payload={"model": "gpt-4o"},
        raw_api_key="secret",
        headers={},
        request_path="/chat/completions",
    )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert response.body == (
        b'{"error":{"message":"Internal server error","type":"server_error","code":"server_error"}}'
    )
