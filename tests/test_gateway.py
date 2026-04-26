from decimal import Decimal

from fastapi import status

from llm_router.domain.enums import ProviderProtocol
from llm_router.domain.schemas import RoutableProvider, RoutedProvider
from llm_router.services.cache.degraded_cache import DegradedType
from llm_router.services.gateway import _degraded_type_for_status, _select_group_provider


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
    assert _degraded_type_for_status(status.HTTP_429_TOO_MANY_REQUESTS) == DegradedType.QUOTA_EXHAUSTED
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
