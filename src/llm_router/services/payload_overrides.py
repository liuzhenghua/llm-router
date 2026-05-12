from __future__ import annotations

from copy import deepcopy
from typing import Any

from llm_router.domain.enums import ProviderProtocol

USER_IMAGE_OMITTED_TEXT = "[User image omitted because the current model does not support image input.]"
TOOL_IMAGE_OMITTED_TEXT = "[Tool returned an image that was omitted because the current model does not support image input.]"


def apply_provider_payload_overrides(payload: dict[str, Any], provider: Any) -> dict[str, Any]:
    if getattr(provider, "strip_image_content", False):
        payload = strip_image_content_from_payload(payload)
    overrides = _overrides_for_provider_protocol(provider)
    if not overrides:
        return payload
    return _deep_merge_payload(payload, overrides)


def strip_image_content_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove image content blocks from chat messages before upstream forwarding."""
    stripped = deepcopy(payload)
    messages = stripped.get("messages")
    if not isinstance(messages, list):
        return stripped

    filtered_messages = []
    for message in messages:
        if not isinstance(message, dict):
            filtered_messages.append(message)
            continue

        filtered_message = _strip_image_content(message)
        image_removed = filtered_message != message

        content = filtered_message.get("content")
        has_content = not (content is None or content == "" or content == [])
        has_non_content_payload = any(
            filtered_message.get(key)
            for key in ("tool_calls", "function_call", "tool_call_id")
        )
        if has_content or has_non_content_payload or "content" not in filtered_message or not image_removed:
            filtered_messages.append(filtered_message)

    stripped["messages"] = filtered_messages
    return stripped


def _strip_image_content(value: Any) -> Any:
    if isinstance(value, list):
        stripped_items = []
        for item in value:
            if isinstance(item, dict) and _is_image_content_block(item):
                continue
            stripped = _strip_image_content(item)
            if stripped is not None:
                stripped_items.append(stripped)
        return stripped_items
    if isinstance(value, dict):
        stripped = {key: _strip_image_content(item) for key, item in value.items()}
        if _is_anthropic_tool_result_with_direct_image(value):
            stripped["content"] = _content_with_omission_text(stripped.get("content"), TOOL_IMAGE_OMITTED_TEXT)
        elif _is_openai_tool_result_with_direct_image(value):
            stripped["content"] = _openai_tool_content_with_omission_text(stripped.get("content"))
        elif _is_user_message_with_direct_image(value):
            stripped["content"] = _content_with_omission_text(stripped.get("content"), USER_IMAGE_OMITTED_TEXT)
        return stripped
    return value


def _is_image_content_block(block: dict[str, Any]) -> bool:
    block_type = block.get("type")
    return block_type in {"image", "image_url", "input_image"}


def _is_anthropic_tool_result_with_direct_image(value: dict[str, Any]) -> bool:
    return (
        value.get("type") == "tool_result"
        and isinstance(value.get("content"), list)
        and _has_direct_image_content(value["content"])
    )


def _is_openai_tool_result_with_direct_image(value: dict[str, Any]) -> bool:
    return (
        value.get("role") == "tool"
        and bool(value.get("tool_call_id"))
        and isinstance(value.get("content"), list)
        and _has_direct_image_content(value["content"])
    )


def _is_user_message_with_direct_image(value: dict[str, Any]) -> bool:
    return (
        value.get("role") == "user"
        and isinstance(value.get("content"), list)
        and _has_direct_image_content(value["content"])
    )


def _has_direct_image_content(content: list[Any]) -> bool:
    return any(isinstance(item, dict) and _is_image_content_block(item) for item in content)


def _content_with_omission_text(content: Any, text: str) -> list[Any]:
    items = content if isinstance(content, list) else []
    return [*items, {"type": "text", "text": text}]


def _openai_tool_content_with_omission_text(content: Any) -> Any:
    if isinstance(content, list) and content:
        return _content_with_omission_text(content, TOOL_IMAGE_OMITTED_TEXT)
    return TOOL_IMAGE_OMITTED_TEXT


def _deep_merge_payload(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge provider payload overrides into a copied request payload."""
    merged = deepcopy(base)
    _merge_into(merged, overrides)
    return merged


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
