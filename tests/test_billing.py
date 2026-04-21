from decimal import Decimal

from llm_router.domain.enums import ProviderProtocol
from llm_router.domain.schemas import RoutedProvider, UsageSnapshot
from llm_router.services.billing import compute_costs


def test_compute_costs_uses_per_million_pricing():
    provider = RoutedProvider(
        id=1,
        name="demo",
        protocol=ProviderProtocol.OPENAI,
        endpoint="https://example.com",
        api_key="secret",
        upstream_model_name="gpt-demo",
        timeout_seconds=60,
        input_token_price=Decimal("2.50"),
        output_token_price=Decimal("10.00"),
        cache_read_token_price=Decimal("1.25"),
        cache_write_token_price=Decimal("0.50"),
        supports_prompt_cache=True,
    )
    usage = UsageSnapshot(
        prompt_tokens=2000,
        completion_tokens=500,
        cache_read_tokens=1000,
        cache_write_tokens=200,
        reasoning_tokens=0,
    )

    costs = compute_costs(provider, usage)

    assert costs.cost_input == Decimal("0.00500")
    assert costs.cost_output == Decimal("0.00500")
    assert costs.cost_cache_read == Decimal("0.00125")
    assert costs.cost_cache_write == Decimal("0.00010")
    assert costs.total_cost == Decimal("0.01135")
