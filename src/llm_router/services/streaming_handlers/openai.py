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


class OpenAIStreamingHandler(BaseStreamingHandler):
    """OpenAI 流式处理器 - 将 chunk 合并为完整的 message"""

    def __init__(self):
        self._chunks: list[dict] = []
        self._usage_dict: dict | None = None
        self._message: dict | None = None
        self._finish_reason = None
        self._model = None
        self._id = None
        self._created = None
        self._upstream_response = None
        self._client = None
        self._started = None
        self._started_at = None

    def prepare_payload(self, payload: dict, provider: Any) -> dict:
        patched = json.loads(json.dumps(payload))
        patched["model"] = provider.upstream_model_name
        stream_options = patched.setdefault("stream_options", {})
        stream_options["include_usage"] = True
        return patched

    def build_upstream_headers(self, provider: Any, context: Any) -> dict:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {provider.api_key}",
        }

    async def process_line(self, line: str) -> StreamChunk | None:
        if line.startswith("data:"):
            data_str = line.split(":", 1)[1].strip()
            if data_str and data_str != "[DONE]":
                data = json.loads(data_str)
                self._chunks.append(data)
                self._merge_chunk(data)
        return None

    def _merge_chunk(self, chunk: dict) -> None:
        if chunk.get("id"):
            self._id = chunk["id"]
        if chunk.get("model"):
            self._model = chunk["model"]
        if chunk.get("created"):
            self._created = chunk["created"]

        if chunk.get("usage"):
            self._usage_dict = chunk["usage"]

        for choice in chunk.get("choices", []):
            delta = choice.get("delta") or {}

            if self._message is None and delta:
                self._message = {}

            if delta.get("content"):
                self._message.setdefault("content", "")
                self._message["content"] += delta["content"]

            if delta.get("role"):
                self._message["role"] = delta["role"]

            if delta.get("tool_calls"):
                self._message.setdefault("tool_calls", [])
                for tc in delta["tool_calls"]:
                    index = tc.get("index", 0)
                    while len(self._message["tool_calls"]) <= index:
                        self._message["tool_calls"].append({
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        })
                    if tc.get("id"):
                        self._message["tool_calls"][index]["id"] = tc["id"]
                    if tc.get("type"):
                        self._message["tool_calls"][index]["type"] = tc["type"]
                    if tc.get("function"):
                        fn = tc["function"]
                        if fn.get("name"):
                            self._message["tool_calls"][index]["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            self._message["tool_calls"][index]["function"]["arguments"] += fn["arguments"]

            if choice.get("finish_reason"):
                self._finish_reason = choice["finish_reason"]

    def get_accumulated_response(self) -> str:
        result = {
            "id": self._id,
            "object": "chat.completion",
            "created": self._created,
            "model": self._model,
            "choices": [
                {
                    "index": 0,
                    "message": self._message or {},
                    "finish_reason": self._finish_reason,
                }
            ],
            "usage": self._usage_dict,
        }
        return json.dumps(result, ensure_ascii=False)

    def get_usage(self) -> UsageSnapshot | None:
        if not self._usage_dict:
            return None
        prompt_token_details = self._usage_dict.get("prompt_tokens_details") or {}
        completion_token_details = self._usage_dict.get("completion_tokens_details") or {}
        return UsageSnapshot(
            prompt_tokens=self._usage_dict.get("prompt_tokens", 0),
            completion_tokens=self._usage_dict.get("completion_tokens", 0),
            cache_read_tokens=prompt_token_details.get("cached_tokens", 0),
            cache_write_tokens=0,
            reasoning_tokens=completion_token_details.get("reasoning_tokens", 0),
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
                    )
                )
                await stream_cm.__aexit__(None, None, None)
                await self._client.aclose()

        return StreamingResponse(
            event_iterator(),
            media_type="text/event-stream",
            headers=self._filter_headers(self._upstream_response.headers),
        )
