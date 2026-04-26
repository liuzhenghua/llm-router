"""Protocol conversion utilities between OpenAI and Anthropic API formats.

Provides:
  - anthropic_to_openai_request / openai_to_anthropic_request
  - openai_to_anthropic_response / anthropic_to_openai_response
  - get_usage_from_openai_response / get_usage_from_anthropic_response
"""
from __future__ import annotations

import json
import time
from typing import Any


# ==================== Request Converters ====================


def anthropic_to_openai_request(payload: dict, upstream_model: str) -> dict:
    """Convert an Anthropic Messages API request to OpenAI Chat Completions format."""
    result: dict[str, Any] = {"model": upstream_model}
    messages: list[dict] = []

    # Handle system field
    system = payload.get("system")
    if system:
        if isinstance(system, str):
            messages.append({"role": "system", "content": system})
        elif isinstance(system, list):
            text = " ".join(b.get("text", "") for b in system if b.get("type") == "text")
            if text:
                messages.append({"role": "system", "content": text})

    # Convert messages
    raw_messages = payload.get("messages", [])
    for i, msg in enumerate(raw_messages):
        role = msg["role"]
        content = msg["content"]
        is_last = (i == len(raw_messages) - 1)

        if isinstance(content, str):
            openai_msg = {"role": role, "content": content}
        elif isinstance(content, list):
            converted = _anthropic_content_blocks_to_openai_messages(role, content)
            # _anthropic_content_blocks_to_openai_messages returns a list;
            # for assistant role it normally returns a single message dict.
            if converted:
                openai_msg = converted[0]
                if len(converted) > 1:
                    messages.extend(converted[:-1])
                    openai_msg = converted[-1]
            else:
                openai_msg = {"role": role, "content": ""}
        else:
            openai_msg = {"role": role, "content": content}

        # Partial Mode: when the last message is from assistant, mark it as partial
        # to enable prefill (leading text) functionality for OpenAI-compatible upstreams.
        if is_last and role == "assistant" and not msg.get("tool_calls"):
            openai_msg["partial"] = True

        messages.append(openai_msg)

    result["messages"] = messages

    # Copy common fields
    if "max_tokens" in payload:
        result["max_tokens"] = payload["max_tokens"]
    if "temperature" in payload:
        result["temperature"] = payload["temperature"]
    if "top_p" in payload:
        result["top_p"] = payload["top_p"]
    if "stop_sequences" in payload:
        result["stop"] = payload["stop_sequences"]
    if "stream" in payload:
        result["stream"] = payload["stream"]

    # Convert tools
    if payload.get("tools"):
        result["tools"] = [_anthropic_tool_to_openai(t) for t in payload["tools"]]
    if payload.get("tool_choice"):
        result["tool_choice"] = _anthropic_tool_choice_to_openai(payload["tool_choice"])

    return result


def openai_to_anthropic_request(payload: dict, upstream_model: str) -> dict:
    """Convert an OpenAI Chat Completions request to Anthropic Messages API format."""
    result: dict[str, Any] = {"model": upstream_model}
    messages: list[dict] = []
    system_parts: list[str] = []

    for msg in payload.get("messages", []):
        role = msg["role"]
        content = msg.get("content")
        tool_calls = msg.get("tool_calls")

        if role == "system":
            if isinstance(content, str):
                system_parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        system_parts.append(part["text"])

        elif role == "tool":
            # OpenAI tool result -> Anthropic tool_result content block
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": content if isinstance(content, str) else json.dumps(content),
                }],
            })

        elif role == "assistant" and tool_calls:
            # Assistant with tool_calls -> tool_use content blocks
            blocks: list[dict] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for tc in tool_calls:
                try:
                    input_data = (
                        json.loads(tc["function"]["arguments"])
                        if isinstance(tc["function"]["arguments"], str)
                        else tc["function"]["arguments"]
                    )
                except Exception:
                    input_data = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "input": input_data,
                })
            messages.append({"role": "assistant", "content": blocks})

        else:
            if isinstance(content, str):
                anthropic_msg = {"role": role, "content": content}
            elif isinstance(content, list):
                anthropic_content = _openai_content_parts_to_anthropic(content)
                anthropic_msg = {"role": role, "content": anthropic_content}
            else:
                anthropic_msg = {"role": role, "content": content or ""}
            messages.append(anthropic_msg)

    # Handle partial mode: if the last message is assistant with partial=True,
    # it represents a prefill/leading text that should be preserved as the last
    # assistant message in Anthropic format (no special flag needed).
    # The partial field is OpenAI-specific and is stripped when converting back.

    if system_parts:
        result["system"] = "\n".join(system_parts)
    result["messages"] = messages

    # Copy common fields
    if "max_tokens" in payload:
        result["max_tokens"] = payload["max_tokens"]
    else:
        result["max_tokens"] = 4096  # Anthropic requires max_tokens
    if "temperature" in payload:
        result["temperature"] = payload["temperature"]
    if "top_p" in payload:
        result["top_p"] = payload["top_p"]
    if "stop" in payload:
        stop = payload["stop"]
        result["stop_sequences"] = stop if isinstance(stop, list) else [stop]
    if "stream" in payload:
        result["stream"] = payload["stream"]

    # Convert tools
    if payload.get("tools"):
        result["tools"] = [_openai_tool_to_anthropic(t) for t in payload["tools"]]
    if payload.get("tool_choice"):
        result["tool_choice"] = _openai_tool_choice_to_anthropic(payload["tool_choice"])

    return result


# ==================== Response Converters ====================


def openai_to_anthropic_response(body: dict, logical_model_name: str) -> dict:
    """Convert an OpenAI Chat Completions response to Anthropic Messages API format."""
    choices = body.get("choices") or []
    choice = choices[0] if choices else {}
    message = choice.get("message") or {}

    content: list[dict] = []
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    for tc in message.get("tool_calls") or []:
        try:
            input_data = (
                json.loads(tc["function"]["arguments"])
                if isinstance(tc["function"]["arguments"], str)
                else tc["function"]["arguments"]
            )
        except Exception:
            input_data = {}
        content.append({
            "type": "tool_use",
            "id": tc["id"],
            "name": tc["function"]["name"],
            "input": input_data,
        })

    finish_reason = choice.get("finish_reason", "stop")
    stop_reason = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "stop_sequence",
    }.get(finish_reason, "end_turn")

    usage = body.get("usage") or {}
    return {
        "id": body.get("id", ""),
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": logical_model_name,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def anthropic_to_openai_response(body: dict) -> dict:
    """Convert an Anthropic Messages API response to OpenAI Chat Completions format."""
    content_blocks = body.get("content") or []
    message_content: str | None = None
    tool_calls: list[dict] = []

    for block in content_blocks:
        if block.get("type") == "text":
            message_content = block["text"]
        elif block.get("type") == "tool_use":
            tool_calls.append({
                "id": block["id"],
                "type": "function",
                "function": {
                    "name": block["name"],
                    "arguments": json.dumps(block.get("input") or {}),
                },
            })

    stop_reason = body.get("stop_reason", "end_turn")
    finish_reason = {
        "end_turn": "stop",
        "max_tokens": "length",
        "tool_use": "tool_calls",
        "stop_sequence": "stop",
    }.get(stop_reason, "stop")

    message: dict[str, Any] = {"role": "assistant"}
    if message_content is not None:
        message["content"] = message_content
    if tool_calls:
        message["tool_calls"] = tool_calls

    usage = body.get("usage") or {}
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)

    return {
        "id": body.get("id", ""),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", ""),
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
            "logprobs": None,
        }],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }


# ==================== Usage Extractors ====================


def get_usage_from_openai_response(body: dict):
    """Extract UsageSnapshot from an OpenAI response body."""
    from llm_router.domain.schemas import UsageSnapshot

    usage_obj = body.get("usage")
    if not usage_obj:
        return None
    prompt_token_details = usage_obj.get("prompt_tokens_details") or {}
    completion_token_details = usage_obj.get("completion_tokens_details") or {}
    return UsageSnapshot(
        prompt_tokens=usage_obj.get("prompt_tokens", 0),
        completion_tokens=usage_obj.get("completion_tokens", 0),
        cache_read_tokens=prompt_token_details.get("cached_tokens", 0),
        cache_write_tokens=0,
        reasoning_tokens=completion_token_details.get("reasoning_tokens", 0),
    )


def get_usage_from_anthropic_response(body: dict):
    """Extract UsageSnapshot from an Anthropic response body."""
    from llm_router.domain.schemas import UsageSnapshot

    usage_obj = body.get("usage") or (body.get("message") or {}).get("usage")
    if not usage_obj:
        return None
    input_tokens = usage_obj.get("input_tokens", 0)
    cache_creation = usage_obj.get("cache_creation_input_tokens", 0)
    cache_read = usage_obj.get("cache_read_input_tokens", 0)
    return UsageSnapshot(
        prompt_tokens=input_tokens + cache_creation + cache_read,
        completion_tokens=usage_obj.get("output_tokens", 0),
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_creation,
        reasoning_tokens=usage_obj.get("reasoning_tokens", 0),
    )


# ==================== Private Helpers ====================


def _anthropic_content_blocks_to_openai_messages(role: str, blocks: list) -> list[dict]:
    """Convert Anthropic content blocks to one or more OpenAI messages."""
    text_parts: list[dict] = []
    tool_calls: list[dict] = []
    text_content: str | None = None

    for block in blocks:
        btype = block.get("type")
        if btype == "text":
            text_parts.append({"type": "text", "text": block["text"]})
            text_content = block["text"]
        elif btype == "image":
            source = block.get("source", {})
            if source.get("type") == "base64":
                text_parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{source['media_type']};base64,{source['data']}"
                    },
                })
            elif source.get("type") == "url":
                text_parts.append({
                    "type": "image_url",
                    "image_url": {"url": source["url"]},
                })
        elif btype == "tool_use" and role == "assistant":
            tool_calls.append({
                "id": block["id"],
                "type": "function",
                "function": {
                    "name": block["name"],
                    "arguments": json.dumps(block.get("input") or {}),
                },
            })
        elif btype == "tool_result" and role == "user":
            result_content = block.get("content", "")
            if isinstance(result_content, list):
                result_content = " ".join(
                    b.get("text", "") for b in result_content if b.get("type") == "text"
                )
            return [{"role": "tool", "tool_call_id": block.get("tool_use_id", ""), "content": result_content}]

    if tool_calls:
        msg: dict[str, Any] = {"role": role, "content": text_content, "tool_calls": tool_calls}
        return [msg]
    if len(text_parts) == 1 and text_parts[0].get("type") == "text":
        return [{"role": role, "content": text_parts[0]["text"]}]
    if text_parts:
        return [{"role": role, "content": text_parts}]
    return [{"role": role, "content": ""}]


def _openai_content_parts_to_anthropic(parts: list) -> list[dict]:
    """Convert OpenAI content parts to Anthropic content blocks."""
    result = []
    for part in parts:
        if part.get("type") == "text":
            result.append({"type": "text", "text": part["text"]})
        elif part.get("type") == "image_url":
            url = part["image_url"]["url"]
            if url.startswith("data:"):
                try:
                    media_type, data = url[5:].split(";base64,", 1)
                    result.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": data},
                    })
                except ValueError:
                    pass
            else:
                result.append({
                    "type": "image",
                    "source": {"type": "url", "url": url},
                })
    return result


def _anthropic_tool_to_openai(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        },
    }


def _openai_tool_to_anthropic(tool: dict) -> dict:
    func = tool.get("function", {})
    return {
        "name": func.get("name", ""),
        "description": func.get("description", ""),
        "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
    }


def _anthropic_tool_choice_to_openai(tc: Any) -> Any:
    if isinstance(tc, str):
        if tc == "any":
            return "required"
        return tc  # "auto" maps as-is
    if isinstance(tc, dict):
        t = tc.get("type")
        if t == "tool":
            return {"type": "function", "function": {"name": tc["name"]}}
        if t == "any":
            return "required"
        if t == "auto":
            return "auto"
    return "auto"


def _openai_tool_choice_to_anthropic(tc: Any) -> Any:
    if isinstance(tc, str):
        if tc == "required":
            return {"type": "any"}
        if tc == "none":
            return {"type": "auto"}
        return {"type": tc}  # "auto" maps as-is
    if isinstance(tc, dict) and tc.get("type") == "function":
        return {"type": "tool", "name": tc["function"]["name"]}
    return {"type": "auto"}
