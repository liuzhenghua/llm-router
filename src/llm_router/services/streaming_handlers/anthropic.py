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
from llm_router.services.post_request import (
    ProviderPricesData,
    RequestFinalizationData,
    UsageSnapshotData,
    schedule_post_request_tasks,
)
from llm_router.services.streaming_handlers.base import BaseStreamingHandler, StreamChunk


class AnthropicStreamingHandler(BaseStreamingHandler):
    """Anthropic 流式处理器 - 将 SSE 事件合并为完整的 message"""

    def __init__(self):
        self._usage_dict: dict | None = None
        self._message_blocks: list[dict] = []
        self._current_event: str | None = None
        self._current_block_type: str | None = None
        self._current_text: str = ""
        self._thinking_content: str = ""
        self._current_tool_name: str = ""
        self._current_tool_args: str = ""
        self._current_tool_call_id: str = ""
        self._final_message: dict | None = None
        self._stop_reason: str | None = None
        self._model: str | None = None
        self._id: str | None = None
        self._type: str | None = None
        self._upstream_response = None
        self._client = None
        self._started = None
        self._started_at = None

    def prepare_payload(self, payload: dict, provider: Any) -> dict:
        patched = json.loads(json.dumps(payload))
        patched["model"] = provider.upstream_model_name
        return patched

    def build_upstream_headers(self, provider: Any, context: Any) -> dict:
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": provider.api_key,
            "anthropic-version": context.headers.get("anthropic-version", "2023-06-01"),
        }
        if beta := context.headers.get("anthropic-beta"):
            headers["anthropic-beta"] = beta
        return headers

    async def process_line(self, line: str) -> StreamChunk | None:
        if line.startswith("event:"):
            self._current_event = line.split(":", 1)[1].strip()
            return None
        elif line.startswith("data:"):
            data_str = line.split(":", 1)[1].strip()
            if data_str:
                data = json.loads(data_str)
                self._process_event(data)
        elif line == "":
            self._current_event = None
        return None

    def _process_event(self, data: dict) -> None:
        event = self._current_event

        if event == "message_start":
            self._id = data.get("message", {}).get("id")
            self._model = data.get("message", {}).get("model")
            self._type = data.get("message", {}).get("type")
            if data.get("message", {}).get("usage"):
                self._usage_dict = data["message"]["usage"]

        elif event == "content_block_start":
            block = data.get("content_block") or {}
            self._current_block_type = block.get("type")
            if self._current_block_type == "text":
                self._current_text = ""
            elif self._current_block_type == "tool_use":
                self._current_tool_name = block.get("name", "")
                self._current_tool_call_id = block.get("id", "")
                self._current_tool_args = ""

        elif event == "content_block_delta":
            delta = data.get("delta") or {}
            if delta.get("type") == "text_delta":
                self._current_text += delta.get("text", "")
            elif delta.get("type") == "input_json_delta":
                self._current_tool_args += delta.get("partial_json", "")
            elif delta.get("type") == "thinking_delta":
                self._thinking_content += delta.get("thinking", "")

        elif event == "content_block_end":
            if self._current_block_type == "text":
                self._message_blocks.append({"type": "text", "text": self._current_text})
            elif self._current_block_type == "tool_use":
                self._message_blocks.append({
                    "type": "tool_use",
                    "id": self._current_tool_call_id,
                    "name": self._current_tool_name,
                    "input": json.loads(self._current_tool_args) if self._current_tool_args else {},
                })
            self._current_block_type = None

        elif event == "message_delta":
            if data.get("usage"):
                self._usage_dict = data["usage"]
            self._stop_reason = data.get("delta", {}).get("stop_reason")

        elif event == "message_end":
            self._final_message = {
                "id": self._id,
                "type": self._type,
                "model": self._model,
                "role": "assistant",
                "content": self._message_blocks,
                "stop_reason": self._stop_reason,
                "stop_sequence": None,
                "usage": self._usage_dict,
            }
            if self._thinking_content:
                self._final_message["thinking"] = self._thinking_content

    def get_accumulated_response(self) -> str:
        if self._final_message:
            return json.dumps(self._final_message, ensure_ascii=False)
        return json.dumps({"content": self._message_blocks}, ensure_ascii=False)

    def get_usage(self) -> UsageSnapshot | None:
        if not self._usage_dict:
            return None
        return UsageSnapshot(
            prompt_tokens=self._usage_dict.get("input_tokens", 0),
            completion_tokens=self._usage_dict.get("output_tokens", 0),
            cache_read_tokens=self._usage_dict.get("cache_read_input_tokens", 0),
            cache_write_tokens=self._usage_dict.get("cache_creation_input_tokens", 0),
            reasoning_tokens=self._usage_dict.get("reasoning_tokens", 0),
        )

    def get_upstream_request_id(self) -> str | None:
        return self._id

    def _filter_headers(self, headers: httpx.Headers) -> dict[str, str]:
        hop_by_hop = {
            "content-length",
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailers",
            "transfer-encoding",
            "upgrade",
            "content-encoding",
        }
        return {k: v for k, v in headers.items() if k.lower() not in hop_by_hop}

    def _create_finalization_data(self, **kwargs) -> RequestFinalizationData:
        request_body = None
        if kwargs.get("request_logging_enabled") and kwargs.get("request_payload"):
            try:
                request_body = json.dumps(kwargs["request_payload"], ensure_ascii=False)
            except Exception:
                pass

        response_body_serialized = kwargs["response_body"] if kwargs.get("response_logging_enabled") else None

        usage_data = None
        if kwargs.get("usage"):
            usage = kwargs["usage"]
            usage_data = UsageSnapshotData(
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
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
        )

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
        full_endpoint = provider.endpoint.rstrip("/") + request_path

        self._client = httpx.AsyncClient(timeout=provider.timeout_seconds)
        stream_cm = self._client.stream("POST", full_endpoint, json=payload, headers=headers)
        self._upstream_response = await stream_cm.__aenter__()

        if self._upstream_response.status_code >= 400:
            detail = (await self._upstream_response.aread()).decode("utf-8")
            await stream_cm.__aexit__(None, None, None)
            await self._client.aclose()
            latency_ms = int((time.perf_counter() - self._started) * 1000)
            schedule_post_request_tasks(
                self._create_finalization_data(
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
                )
            )
            raise HTTPException(status_code=self._upstream_response.status_code, detail=detail)

        async def event_iterator():
            stream_failed = False
            error_message = ""
            try:
                async for line in self._upstream_response.aiter_lines():
                    raw = f"{line}\n"
                    await self.process_line(line)
                    yield raw.encode("utf-8")
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

                schedule_post_request_tasks(
                    self._create_finalization_data(
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
                    )
                )
                await stream_cm.__aexit__(None, None, None)
                await self._client.aclose()

        return StreamingResponse(
            event_iterator(),
            media_type="text/event-stream",
            headers=self._filter_headers(self._upstream_response.headers),
        )
