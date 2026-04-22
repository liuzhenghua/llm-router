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
    reasoning_tokens: int = 0


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


# ==================== 缓存数据结构 ====================


@dataclass(slots=True)
class CachedApiKey:
    """ApiKey 缓存数据（不含敏感 key_hash）"""
    id: int
    name: str
    status: str  # "active", "disabled", "deleted"
    balance: Decimal
    daily_budget_limit: Decimal | None
    daily_spend_amount: Decimal
    daily_spend_date: str | None  # ISO date string, e.g. "2025-01-15"
    qps_limit: int
    allowed_logical_models_json: list[str]  # JSON array stored as list

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "balance": str(self.balance),
            "daily_budget_limit": str(self.daily_budget_limit) if self.daily_budget_limit is not None else None,
            "daily_spend_amount": str(self.daily_spend_amount),
            "daily_spend_date": self.daily_spend_date,
            "qps_limit": self.qps_limit,
            "allowed_logical_models_json": self.allowed_logical_models_json,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CachedApiKey:
        return cls(
            id=d["id"],
            name=d["name"],
            status=d["status"],
            balance=Decimal(d["balance"]),
            daily_budget_limit=Decimal(d["daily_budget_limit"]) if d.get("daily_budget_limit") else None,
            daily_spend_amount=Decimal(d["daily_spend_amount"]),
            daily_spend_date=d.get("daily_spend_date"),
            qps_limit=d["qps_limit"],
            allowed_logical_models_json=d.get("allowed_logical_models_json") or [],
        )


@dataclass(slots=True)
class CachedRoute:
    """路由缓存数据"""
    route_id: int
    logical_model_id: int
    provider_model_id: int
    priority: int
    weight: int
    is_fallback: bool
    status: str  # "active", "disabled"

    def to_dict(self) -> dict:
        return {
            "route_id": self.route_id,
            "logical_model_id": self.logical_model_id,
            "provider_model_id": self.provider_model_id,
            "priority": self.priority,
            "weight": self.weight,
            "is_fallback": self.is_fallback,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CachedRoute:
        return cls(
            route_id=d["route_id"],
            logical_model_id=d["logical_model_id"],
            provider_model_id=d["provider_model_id"],
            priority=d["priority"],
            weight=d["weight"],
            is_fallback=d.get("is_fallback", False),
            status=d["status"],
        )


@dataclass(slots=True)
class CachedProvider:
    """Provider 缓存数据（encrypted_api_key 缓存后解密使用）"""
    id: int
    name: str
    openai_endpoint: str | None
    anthropic_endpoint: str | None
    encrypted_api_key: str  # 缓存加密后的，调用时解密
    upstream_model_name: str
    input_token_price: Decimal
    output_token_price: Decimal
    cache_read_token_price: Decimal
    cache_write_token_price: Decimal
    supports_prompt_cache: bool
    timeout_seconds: int
    is_active: bool

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "openai_endpoint": self.openai_endpoint,
            "anthropic_endpoint": self.anthropic_endpoint,
            "encrypted_api_key": self.encrypted_api_key,
            "upstream_model_name": self.upstream_model_name,
            "input_token_price": str(self.input_token_price),
            "output_token_price": str(self.output_token_price),
            "cache_read_token_price": str(self.cache_read_token_price),
            "cache_write_token_price": str(self.cache_write_token_price),
            "supports_prompt_cache": self.supports_prompt_cache,
            "timeout_seconds": self.timeout_seconds,
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CachedProvider:
        return cls(
            id=d["id"],
            name=d["name"],
            openai_endpoint=d.get("openai_endpoint"),
            anthropic_endpoint=d.get("anthropic_endpoint"),
            encrypted_api_key=d["encrypted_api_key"],
            upstream_model_name=d["upstream_model_name"],
            input_token_price=Decimal(d["input_token_price"]),
            output_token_price=Decimal(d["output_token_price"]),
            cache_read_token_price=Decimal(d["cache_read_token_price"]),
            cache_write_token_price=Decimal(d["cache_write_token_price"]),
            supports_prompt_cache=d.get("supports_prompt_cache", False),
            timeout_seconds=d.get("timeout_seconds", 60),
            is_active=d["is_active"],
        )


# ==================== 路由策略数据结构 ====================


@dataclass(slots=True)
class RoutableProvider:
    """可路由的 provider，包含路由 ID 和权重信息"""
    route_id: int
    provider: RoutedProvider
    weight: int  # 权重，0 表示不参与路由

    def to_tuple(self) -> tuple[int, RoutedProvider, int]:
        """返回 (route_id, provider, weight) 元组"""
        return (self.route_id, self.provider, self.weight)


@dataclass(slots=True)
class RoutableProviderGroup:
    """
    同一优先级的 provider 组

    同组内按权重分配流量，不同组按优先级顺序调用
    """
    priority: int
    is_fallback: bool
    providers: list[RoutableProvider]

    @property
    def total_weight(self) -> int:
        """组内总权重"""
        return sum(p.weight for p in self.providers if p.weight > 0)
