"""Cross-protocol streaming handlers.

AnthropicOverOpenAIStreamingHandler:
    Client speaks Anthropic (streaming) → converts request to OpenAI → calls OpenAI upstream
    (streaming) → converts each OpenAI SSE chunk to Anthropic SSE events in real-time →
    streams Anthropic SSE to client.

OpenAIOverAnthropicStreamingHandler:
    Client speaks OpenAI (streaming) → converts request to Anthropic → calls Anthropic upstream
    (streaming) → converts each Anthropic SSE event to OpenAI SSE chunks in real-time →
    streams OpenAI SSE to client.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx
from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from llm_router.domain.enums import ProviderProtocol
from llm_router.domain.models import utcnow
from llm_router.domain.schemas import UsageSnapshot
from llm_router.services.post_request import RequestFinalizationData, schedule_post_request_tasks
from llm_router.services.protocol_converter import (
    anthropic_to_openai_request,
    get_usage_from_anthropic_response,
    get_usage_from_openai_response,
    openai_to_anthropic_request,
)
from llm_router.services.streaming_handlers.base import BaseStreamingHandler, StreamChunk

_HOP_BY_HOP = {
    "content-length", "connection", "keep-alive",
    "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-encoding",
}


def _filter_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


def _build_finalization_data(**kwargs) -> RequestFinalizationData:
    from llm_router.services.post_request import ProviderPricesData, UsageSnapshotData

    request_body = None
    if kwargs.get("request_logging_enabled") and kwargs.get("request_payload"):
        try:
            request_body = json.dumps(kwargs["request_payload"], ensure_ascii=False)
        except Exception:
            pass

    response_body_serialized = kwargs["response_body"] if kwargs.get("response_logging_enabled") else None

    usage_data = None
    if kwargs.get("usage"):
        u = kwargs["usage"]
        usage_data = UsageSnapshotData(
            prompt_tokens=u.prompt_tokens,
            completion_tokens=u.completion_tokens,
            cache_read_tokens=u.cache_read_tokens,
            cache_write_tokens=u.cache_write_tokens,
            reasoning_tokens=u.reasoning_tokens,
        )

    prices_data = ProviderPricesData(
        input_token_price=kwargs["provider"].input_token_price,
        output_token_price=kwargs["provider"].output_token_price,
        cache_read_token_price=kwargs["provider"].cache_read_token_price,
        cache_write_token_price=kwargs["provider"].cache_write_token_price,
    )

    return RequestFinalizationData(
        request_id=kwargs["request_id"],
        upstream_request_id=kwargs["upstream_request_id"],
        api_key_id=kwargs["api_key_id"],
        logical_model_id=kwargs["logical_model_id"],
        provider_model_id=kwargs["provider_model_id"],
        protocol=kwargs["protocol"].value,
        call_type=kwargs["call_type"],
        status_code=kwargs["status_code"],
        success=kwargs["success"],
        latency_ms=kwargs["latency_ms"],
        request_body=request_body,
        response_body=response_body_serialized,
        error_message=kwargs["error_message"],
        started_at=kwargs.get("started_at"),
        ended_at=kwargs.get("ended_at"),
        usage=usage_data,
        provider_prices=prices_data,
        end_user=kwargs.get("end_user"),
        channel=kwargs.get("channel"),
        api_key_timezone=kwargs.get("api_key_timezone", "UTC"),
    )


# ===========================================================================
#  AnthropicOverOpenAIStreamingHandler
#  Client: Anthropic streaming  →  Upstream: OpenAI streaming
# ===========================================================================

class AnthropicOverOpenAIStreamingHandler(BaseStreamingHandler):
    """Anthropic client (streaming) → OpenAI upstream (streaming).

    State machine:
      - Receives OpenAI SSE data lines.
      - Translates them into Anthropic SSE events (message_start,
        content_block_start/delta/stop, message_delta, message_stop).
    """

    _UPSTREAM_PATH = "/chat/completions"

    def __init__(self):
        # Accumulated metadata
        self._msg_id: str | None = None
        self._model: str | None = None
        self._finish_reason: str | None = None
        self._usage_dict: dict | None = None
        self._accumulated_content: str = ""
        self._current_tool_calls: list[dict] = []  # [{id, name, arguments}]

        # SSE state tracking
        self._message_started: bool = False
        self._next_block_index: int = 0        # Anthropic block index counter
        self._text_block_open: bool = False
        self._tool_block_map: dict[int, int] = {}  # openai_tc_index → anthropic_block_index

        # httpx handles
        self._started: float | None = None
        self._started_at = None
        self._upstream_response = None
        self._client = None

    # ---- BaseStreamingHandler interface (minimal; proxy() is fully overridden) ----

    def prepare_payload(self, payload: dict, provider: Any) -> dict:
        openai_payload = anthropic_to_openai_request(payload, provider.upstream_model_name)
        openai_payload["stream"] = True
        openai_payload.setdefault("stream_options", {})["include_usage"] = True
        return openai_payload

    def build_upstream_headers(self, provider: Any, context: Any) -> dict:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {provider.api_key}",
        }

    async def process_line(self, line: str) -> StreamChunk | None:
        return None  # Not used; proxy() handles lines directly.

    def get_accumulated_response(self) -> str:
        content: list[dict] = []
        if self._accumulated_content:
            content.append({"type": "text", "text": self._accumulated_content})
        for tc in self._current_tool_calls:
            content.append({
                "type": "tool_use",
                "id": tc.get("id", ""),
                "name": tc.get("name", ""),
                "input": _safe_json_loads(tc.get("arguments", "{}")),
            })
        stop_reason = {
            "stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use",
        }.get(self._finish_reason or "stop", "end_turn")
        usage = self._usage_dict or {}
        result = {
            "id": self._msg_id,
            "type": "message",
            "role": "assistant",
            "model": self._model,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            },
        }
        return json.dumps(result, ensure_ascii=False)

    def get_usage(self) -> UsageSnapshot | None:
        return get_usage_from_openai_response({"usage": self._usage_dict}) if self._usage_dict else None

    def get_upstream_request_id(self) -> str | None:
        return self._msg_id

    # ---- SSE conversion helpers ----

    def _sse(self, event: str, data: dict) -> bytes:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")

    def _ensure_message_started(self) -> list[bytes]:
        """Emit message_start if not already sent."""
        if self._message_started:
            return []
        self._message_started = True
        return [self._sse("message_start", {
            "type": "message_start",
            "message": {
                "id": self._msg_id or "",
                "type": "message",
                "role": "assistant",
                "model": self._model or "",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        })]

    def _ensure_text_block_open(self) -> list[bytes]:
        """Open a text content block if none is open."""
        if self._text_block_open:
            return []
        idx = self._next_block_index
        self._next_block_index += 1
        self._text_block_open = True
        return [self._sse("content_block_start", {
            "type": "content_block_start",
            "index": idx,
            "content_block": {"type": "text", "text": ""},
        })]

    def _close_text_block_if_open(self) -> list[bytes]:
        if not self._text_block_open:
            return []
        # The text block index is (next_block_index - 1) if we allocated one
        idx = self._next_block_index - 1
        self._text_block_open = False
        return [self._sse("content_block_stop", {"type": "content_block_stop", "index": idx})]

    def _process_openai_chunk(self, chunk: dict) -> list[bytes]:
        """Convert a single OpenAI SSE chunk to a list of Anthropic SSE event bytes."""
        events: list[bytes] = []

        # Extract metadata on first chunk
        if chunk.get("id") and not self._msg_id:
            self._msg_id = chunk["id"]
        if chunk.get("model") and not self._model:
            self._model = chunk["model"]
        if chunk.get("usage"):
            self._usage_dict = chunk["usage"]

        choices = chunk.get("choices") or []
        if not choices:
            return events

        choice = choices[0]
        delta = choice.get("delta") or {}
        finish_reason = choice.get("finish_reason")

        # Ensure message_start is emitted before any content
        if delta.get("role") or delta.get("content") or delta.get("tool_calls") or finish_reason:
            events.extend(self._ensure_message_started())

        # --- Text content ---
        if delta.get("content"):
            events.extend(self._ensure_text_block_open())
            self._accumulated_content += delta["content"]
            # Use (next_block_index - 1) as the current text block index
            events.append(self._sse("content_block_delta", {
                "type": "content_block_delta",
                "index": self._next_block_index - 1,
                "delta": {"type": "text_delta", "text": delta["content"]},
            }))

        # --- Tool calls ---
        for tc_delta in delta.get("tool_calls") or []:
            tc_idx = tc_delta.get("index", 0)

            if tc_idx not in self._tool_block_map:
                # Close text block if open before starting tool block
                events.extend(self._close_text_block_if_open())

                # Allocate new Anthropic block for this tool call
                block_idx = self._next_block_index
                self._next_block_index += 1
                self._tool_block_map[tc_idx] = block_idx
                self._current_tool_calls.append({"id": "", "name": "", "arguments": ""})

                events.append(self._sse("content_block_start", {
                    "type": "content_block_start",
                    "index": block_idx,
                    "content_block": {
                        "type": "tool_use",
                        "id": tc_delta.get("id", ""),
                        "name": (tc_delta.get("function") or {}).get("name", ""),
                        "input": {},
                    },
                }))

            block_idx = self._tool_block_map[tc_idx]
            tc = self._current_tool_calls[tc_idx]

            if tc_delta.get("id"):
                tc["id"] = tc_delta["id"]
            fn = tc_delta.get("function") or {}
            if fn.get("name"):
                tc["name"] += fn["name"]
            if fn.get("arguments"):
                tc["arguments"] += fn["arguments"]
                events.append(self._sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": block_idx,
                    "delta": {"type": "input_json_delta", "partial_json": fn["arguments"]},
                }))

        # --- Finish ---
        if finish_reason:
            self._finish_reason = finish_reason

        return events

    def _build_finish_events(self) -> list[bytes]:
        """Emit closing Anthropic events after [DONE]."""
        events: list[bytes] = []

        # Guard: emit message_start if we never got any content
        events.extend(self._ensure_message_started())

        # Close last open block
        if self._text_block_open:
            events.extend(self._close_text_block_if_open())
        elif self._tool_block_map:
            last_tool_block_idx = max(self._tool_block_map.values())
            events.append(self._sse("content_block_stop", {
                "type": "content_block_stop",
                "index": last_tool_block_idx,
            }))

        stop_reason = {
            "stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use",
        }.get(self._finish_reason or "stop", "end_turn")

        usage_dict = self._usage_dict or {}
        events.append(self._sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": usage_dict.get("completion_tokens", 0)},
        }))
        events.append(self._sse("message_stop", {"type": "message_stop"}))
        return events

    # ---- Main proxy ----

    async def proxy(
        self,
        session: AsyncSession,
        *,
        api_key: Any,
        context: Any,
        provider: Any,
        request_path: str,
    ):
        payload = self.prepare_payload(context.payload, provider)
        headers = self.build_upstream_headers(provider, context)
        self._started = time.perf_counter()
        self._started_at = utcnow()
        full_endpoint = provider.endpoint.rstrip("/") + self._UPSTREAM_PATH

        self._client = httpx.AsyncClient(timeout=provider.timeout_seconds)
        stream_cm = self._client.stream("POST", full_endpoint, json=payload, headers=headers)
        self._upstream_response = await stream_cm.__aenter__()

        if self._upstream_response.status_code >= 400:
            detail = (await self._upstream_response.aread()).decode("utf-8")
            await stream_cm.__aexit__(None, None, None)
            await self._client.aclose()
            latency_ms = int((time.perf_counter() - self._started) * 1000)
            schedule_post_request_tasks(_build_finalization_data(
                request_id=context.request_id,
                upstream_request_id=self.get_upstream_request_id(),
                api_key_id=context.api_key_id,
                logical_model_id=context.logical_model_id,
                provider_model_id=provider.id,
                protocol=ProviderProtocol.ANTHROPIC,
                call_type="acompletion",
                status_code=self._upstream_response.status_code,
                success=False,
                latency_ms=latency_ms,
                request_payload=payload,
                response_body=None,
                error_message=detail,
                request_logging_enabled=context.request_logging_enabled,
                response_logging_enabled=context.response_logging_enabled,
                usage=None,
                provider=provider,
                started_at=self._started_at,
                ended_at=utcnow(),
                end_user=context.end_user,
                channel=context.channel,
                api_key_timezone=context.api_key_timezone,
            ))
            raise HTTPException(status_code=self._upstream_response.status_code, detail=detail)

        async def event_iterator():
            stream_failed = False
            error_message = ""
            try:
                async for line in self._upstream_response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line.split(":", 1)[1].strip()
                    if not data_str:
                        continue
                    if data_str == "[DONE]":
                        for ev in self._build_finish_events():
                            yield ev
                    else:
                        try:
                            chunk = json.loads(data_str)
                            for ev in self._process_openai_chunk(chunk):
                                yield ev
                        except json.JSONDecodeError:
                            pass
            except HTTPException:
                stream_failed = True
                error_message = "Upstream stream interrupted"
                raise
            except Exception as exc:
                stream_failed = True
                error_message = str(exc)
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
            finally:
                latency_ms = int((time.perf_counter() - self._started) * 1000)
                response_body = self.get_accumulated_response() if not stream_failed else None
                usage = self.get_usage()
                schedule_post_request_tasks(_build_finalization_data(
                    request_id=context.request_id,
                    upstream_request_id=self.get_upstream_request_id(),
                    api_key_id=context.api_key_id,
                    logical_model_id=context.logical_model_id,
                    provider_model_id=provider.id,
                    protocol=ProviderProtocol.ANTHROPIC,
                    call_type="acompletion",
                    status_code=self._upstream_response.status_code if not stream_failed else status.HTTP_502_BAD_GATEWAY,
                    success=not stream_failed,
                    latency_ms=latency_ms,
                    request_payload=payload,
                    response_body=response_body,
                    error_message=error_message or None,
                    request_logging_enabled=context.request_logging_enabled,
                    response_logging_enabled=context.response_logging_enabled,
                    usage=usage,
                    provider=provider,
                    started_at=self._started_at,
                    ended_at=utcnow(),
                    end_user=context.end_user,
                    channel=context.channel,
                    api_key_timezone=context.api_key_timezone,
                ))
                await stream_cm.__aexit__(None, None, None)
                await self._client.aclose()

        return StreamingResponse(
            event_iterator(),
            media_type="text/event-stream",
        )


# ===========================================================================
#  OpenAIOverAnthropicStreamingHandler
#  Client: OpenAI streaming  →  Upstream: Anthropic streaming
# ===========================================================================

class OpenAIOverAnthropicStreamingHandler(BaseStreamingHandler):
    """OpenAI client (streaming) → Anthropic upstream (streaming).

    Receives Anthropic SSE events and converts them to OpenAI SSE chunks.
    """

    _UPSTREAM_PATH = "/v1/messages"

    def __init__(self):
        # Accumulated metadata
        self._msg_id: str | None = None
        self._model: str | None = None
        self._stop_reason: str | None = None
        self._usage_dict: dict | None = None
        self._accumulated_content: str = ""
        self._tool_call_counter: int = 0  # number of tool_use blocks seen so far

        # Per-block tool call state
        self._current_block_type: str | None = None
        self._current_tool_id: str = ""
        self._current_tool_name: str = ""
        self._current_tool_args: str = ""

        # Anthropic SSE state
        self._current_event: str | None = None

        # Finalized tool calls for response reconstruction
        self._tool_calls: list[dict] = []

        # httpx handles
        self._started: float | None = None
        self._started_at = None
        self._upstream_response = None
        self._client = None

    # ---- BaseStreamingHandler interface ----

    def prepare_payload(self, payload: dict, provider: Any) -> dict:
        anthropic_payload = openai_to_anthropic_request(payload, provider.upstream_model_name)
        anthropic_payload["stream"] = True
        return anthropic_payload

    def build_upstream_headers(self, provider: Any, context: Any) -> dict:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": provider.api_key,
            "anthropic-version": "2023-06-01",
        }

    async def process_line(self, line: str) -> StreamChunk | None:
        return None  # Not used; proxy() handles lines directly.

    def get_accumulated_response(self) -> str:
        message_content: str | None = self._accumulated_content or None
        finish_reason = {
            "end_turn": "stop", "max_tokens": "length",
            "tool_use": "tool_calls", "stop_sequence": "stop",
        }.get(self._stop_reason or "end_turn", "stop")

        msg: dict[str, Any] = {"role": "assistant"}
        if message_content:
            msg["content"] = message_content
        if self._tool_calls:
            msg["tool_calls"] = self._tool_calls

        usage = self._usage_dict or {}
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)

        result = {
            "id": self._msg_id or "",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self._model or "",
            "choices": [{
                "index": 0,
                "message": msg,
                "finish_reason": finish_reason,
                "logprobs": None,
            }],
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
        }
        return json.dumps(result, ensure_ascii=False)

    def get_usage(self) -> UsageSnapshot | None:
        return get_usage_from_anthropic_response({"usage": self._usage_dict}) if self._usage_dict else None

    def get_upstream_request_id(self) -> str | None:
        return self._msg_id

    # ---- SSE conversion helpers ----

    def _openai_sse(self, data: dict) -> bytes:
        """Format a single OpenAI SSE data line."""
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")

    def _base_chunk(self) -> dict:
        return {
            "id": self._msg_id or "",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": self._model or "",
        }

    def _process_anthropic_event(self, event_type: str, data: dict) -> list[bytes]:
        """Convert a single Anthropic SSE event to OpenAI SSE chunk bytes."""
        chunks: list[bytes] = []

        if event_type == "message_start":
            msg = data.get("message") or {}
            self._msg_id = msg.get("id")
            self._model = msg.get("model")
            if msg.get("usage"):
                self._usage_dict = dict(msg["usage"])
            # Emit the initial role chunk
            chunk = self._base_chunk()
            chunk["choices"] = [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]
            chunks.append(self._openai_sse(chunk))

        elif event_type == "content_block_start":
            block = data.get("content_block") or {}
            self._current_block_type = block.get("type")
            if self._current_block_type == "tool_use":
                self._current_tool_id = block.get("id", "")
                self._current_tool_name = block.get("name", "")
                self._current_tool_args = ""
                # Emit tool call header chunk
                tc_index = self._tool_call_counter
                chunk = self._base_chunk()
                chunk["choices"] = [{
                    "index": 0,
                    "delta": {
                        "tool_calls": [{
                            "index": tc_index,
                            "id": self._current_tool_id,
                            "type": "function",
                            "function": {"name": self._current_tool_name, "arguments": ""},
                        }],
                    },
                    "finish_reason": None,
                }]
                chunks.append(self._openai_sse(chunk))

        elif event_type == "content_block_delta":
            delta = data.get("delta") or {}
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                self._accumulated_content += text
                chunk = self._base_chunk()
                chunk["choices"] = [{"index": 0, "delta": {"content": text}, "finish_reason": None}]
                chunks.append(self._openai_sse(chunk))
            elif delta.get("type") == "input_json_delta":
                partial = delta.get("partial_json", "")
                self._current_tool_args += partial
                tc_index = self._tool_call_counter
                chunk = self._base_chunk()
                chunk["choices"] = [{
                    "index": 0,
                    "delta": {
                        "tool_calls": [{
                            "index": tc_index,
                            "function": {"arguments": partial},
                        }],
                    },
                    "finish_reason": None,
                }]
                chunks.append(self._openai_sse(chunk))

        elif event_type == "content_block_stop":
            if self._current_block_type == "tool_use":
                # Finalize this tool call for response reconstruction
                self._tool_calls.append({
                    "id": self._current_tool_id,
                    "type": "function",
                    "function": {
                        "name": self._current_tool_name,
                        "arguments": self._current_tool_args,
                    },
                })
                self._tool_call_counter += 1
            self._current_block_type = None

        elif event_type == "message_delta":
            if data.get("usage"):
                if self._usage_dict is None:
                    self._usage_dict = {}
                self._usage_dict.update(data["usage"])
            self._stop_reason = (data.get("delta") or {}).get("stop_reason")

        elif event_type == "message_stop":
            # Emit finish_reason chunk
            finish_reason = {
                "end_turn": "stop", "max_tokens": "length",
                "tool_use": "tool_calls", "stop_sequence": "stop",
            }.get(self._stop_reason or "end_turn", "stop")

            usage_dict = self._usage_dict or {}
            input_tokens = usage_dict.get("input_tokens", 0)
            output_tokens = usage_dict.get("output_tokens", 0)

            chunk = self._base_chunk()
            chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": finish_reason}]
            chunk["usage"] = {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            }
            chunks.append(self._openai_sse(chunk))
            chunks.append(b"data: [DONE]\n\n")

        return chunks

    # ---- Main proxy ----

    async def proxy(
        self,
        session: AsyncSession,
        *,
        api_key: Any,
        context: Any,
        provider: Any,
        request_path: str,
    ):
        payload = self.prepare_payload(context.payload, provider)
        headers = self.build_upstream_headers(provider, context)
        self._started = time.perf_counter()
        self._started_at = utcnow()
        full_endpoint = provider.endpoint.rstrip("/") + self._UPSTREAM_PATH

        self._client = httpx.AsyncClient(timeout=provider.timeout_seconds)
        stream_cm = self._client.stream("POST", full_endpoint, json=payload, headers=headers)
        self._upstream_response = await stream_cm.__aenter__()

        if self._upstream_response.status_code >= 400:
            detail = (await self._upstream_response.aread()).decode("utf-8")
            await stream_cm.__aexit__(None, None, None)
            await self._client.aclose()
            latency_ms = int((time.perf_counter() - self._started) * 1000)
            schedule_post_request_tasks(_build_finalization_data(
                request_id=context.request_id,
                upstream_request_id=self.get_upstream_request_id(),
                api_key_id=context.api_key_id,
                logical_model_id=context.logical_model_id,
                provider_model_id=provider.id,
                protocol=ProviderProtocol.OPENAI,
                call_type="acompletion",
                status_code=self._upstream_response.status_code,
                success=False,
                latency_ms=latency_ms,
                request_payload=payload,
                response_body=None,
                error_message=detail,
                request_logging_enabled=context.request_logging_enabled,
                response_logging_enabled=context.response_logging_enabled,
                usage=None,
                provider=provider,
                started_at=self._started_at,
                ended_at=utcnow(),
                end_user=context.end_user,
                channel=context.channel,
                api_key_timezone=context.api_key_timezone,
            ))
            raise HTTPException(status_code=self._upstream_response.status_code, detail=detail)

        async def event_iterator():
            stream_failed = False
            error_message = ""
            try:
                async for line in self._upstream_response.aiter_lines():
                    if line.startswith("event:"):
                        self._current_event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        data_str = line.split(":", 1)[1].strip()
                        if data_str and self._current_event:
                            try:
                                data = json.loads(data_str)
                                for chunk_bytes in self._process_anthropic_event(self._current_event, data):
                                    yield chunk_bytes
                            except json.JSONDecodeError:
                                pass
                    elif line == "":
                        self._current_event = None
            except HTTPException:
                stream_failed = True
                error_message = "Upstream stream interrupted"
                raise
            except Exception as exc:
                stream_failed = True
                error_message = str(exc)
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
            finally:
                latency_ms = int((time.perf_counter() - self._started) * 1000)
                response_body = self.get_accumulated_response() if not stream_failed else None
                usage = self.get_usage()
                schedule_post_request_tasks(_build_finalization_data(
                    request_id=context.request_id,
                    upstream_request_id=self.get_upstream_request_id(),
                    api_key_id=context.api_key_id,
                    logical_model_id=context.logical_model_id,
                    provider_model_id=provider.id,
                    protocol=ProviderProtocol.OPENAI,
                    call_type="acompletion",
                    status_code=self._upstream_response.status_code if not stream_failed else status.HTTP_502_BAD_GATEWAY,
                    success=not stream_failed,
                    latency_ms=latency_ms,
                    request_payload=payload,
                    response_body=response_body,
                    error_message=error_message or None,
                    request_logging_enabled=context.request_logging_enabled,
                    response_logging_enabled=context.response_logging_enabled,
                    usage=usage,
                    provider=provider,
                    started_at=self._started_at,
                    ended_at=utcnow(),
                    end_user=context.end_user,
                    channel=context.channel,
                    api_key_timezone=context.api_key_timezone,
                ))
                await stream_cm.__aexit__(None, None, None)
                await self._client.aclose()

        return StreamingResponse(
            event_iterator(),
            media_type="text/event-stream",
        )


# ---- Utilities ----

def _safe_json_loads(s: str) -> Any:
    try:
        return json.loads(s)
    except Exception:
        return {}
