from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from llm_router.domain.enums import ProviderProtocol


@dataclass(slots=True)
class UsageSnapshot:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(slots=True)
class RoutedProvider:
    id: int
    name: str
    protocol: ProviderProtocol
    endpoint: str
    api_key: str
    upstream_model_name: str
    timeout_seconds: int
    input_token_price: Decimal
    output_token_price: Decimal
    cache_read_token_price: Decimal
    cache_write_token_price: Decimal
    supports_prompt_cache: bool


@dataclass(slots=True)
class RequestContext:
    request_id: str
    protocol: ProviderProtocol
    logical_model_name: str
    payload: dict[str, Any]
    stream: bool
    request_logging_enabled: bool
    response_logging_enabled: bool
    api_key_id: int
    api_key_name: str
    logical_model_id: int
    raw_authorization: str | None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class BillingResult:
    total_cost: Decimal
    cost_input: Decimal
    cost_output: Decimal
    cost_cache_read: Decimal
    cost_cache_write: Decimal
