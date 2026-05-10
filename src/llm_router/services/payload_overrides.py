from __future__ import annotations

from copy import deepcopy
from typing import Any

from llm_router.domain.enums import ProviderProtocol


def deep_merge_payload(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge provider payload overrides into a copied request payload."""
    merged = deepcopy(base)
    _merge_into(merged, overrides)
    return merged


def apply_provider_payload_overrides(payload: dict[str, Any], provider: Any) -> dict[str, Any]:
    overrides = _overrides_for_provider_protocol(provider)
    if not overrides:
        return payload
    return deep_merge_payload(payload, overrides)


def _overrides_for_provider_protocol(provider: Any) -> dict[str, Any]:
    if provider.upstream_protocol == ProviderProtocol.OPENAI:
        return getattr(provider, "openai_payload_overrides", None) or {}
    if provider.upstream_protocol == ProviderProtocol.ANTHROPIC:
        return getattr(provider, "anthropic_payload_overrides", None) or {}
    return {}


def _merge_into(base: dict[str, Any], overrides: dict[str, Any]) -> None:
    for key, value in overrides.items():
        existing = base.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            _merge_into(existing, value)
        else:
            base[key] = deepcopy(value)
