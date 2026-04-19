import json
import time
from typing import Any

import httpx
from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from llm_router.domain.enums import ProviderProtocol
from llm_router.domain.schemas import UsageSnapshot
from llm_router.services.non_stream_handlers.base import BaseNonStreamHandler
from llm_router.services.post_request import (
    RequestFinalizationData,
    schedule_post_request_tasks,
)


class OpenAINonStreamHandler(BaseNonStreamHandler):
    """OpenAI 非流式处理器"""

    def prepare_payload(self, payload: dict, provider: Any) -> dict:
        patched = json.loads(json.dumps(payload))
        patched["model"] = provider.upstream_model_name
        return patched

    def build_upstream_headers(self, provider: Any, context: Any) -> dict:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {provider.api_key}",
        }

    def get_usage(self, body: dict) -> UsageSnapshot | None:
        usage_obj = body.get("usage")
        if not usage_obj:
            return None
        details = usage_obj.get("prompt_tokens_details") or {}
        return UsageSnapshot(
            prompt_tokens=usage_obj.get("prompt_tokens", 0),
            completion_tokens=usage_obj.get("completion_tokens", 0),
            cache_read_tokens=details.get("cached_tokens", 0),
            cache_write_tokens=0,
        )

    def get_upstream_request_id(self, body: dict, headers: httpx.Headers) -> str | None:
        # 优先从 body 获取
        if body.get("id"):
            return body["id"]
        # 降级到响应头
        return headers.get("x-request-id") or headers.get("request-id")

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
        started = time.perf_counter()
        full_endpoint = provider.endpoint.rstrip("/") + request_path

        try:
            async with httpx.AsyncClient(timeout=provider.timeout_seconds) as client:
                response = await client.post(full_endpoint, json=payload, headers=headers)

            if response.status_code >= 400:
                raise HTTPException(status_code=response.status_code, detail=response.text)

            body = response.json()
            usage = self.get_usage(body)
            upstream_request_id = self.get_upstream_request_id(body, response.headers)
            latency_ms = int((time.perf_counter() - started) * 1000)

            schedule_post_request_tasks(
                self._create_finalization_data(
                    request_id=context.request_id,
                    upstream_request_id=upstream_request_id,
                    api_key_id=context.api_key_id,
                    logical_model_id=context.logical_model_id,
                    provider_model_id=provider.id,
                    protocol=ProviderProtocol.OPENAI,
                    call_type="completion",
                    status_code=response.status_code,
                    success=True,
                    latency_ms=latency_ms,
                    request_payload=payload,
                    response_body=json.dumps(body, ensure_ascii=False),
                    error_message=None,
                    request_logging_enabled=context.request_logging_enabled,
                    response_logging_enabled=context.response_logging_enabled,
                    usage=usage,
                    provider=provider,
                )
            )

            return JSONResponse(content=body, status_code=response.status_code, headers=self._filter_headers(response.headers))

        except HTTPException:
            raise
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            schedule_post_request_tasks(
                self._create_finalization_data(
                    request_id=context.request_id,
                    upstream_request_id=None,
                    api_key_id=context.api_key_id,
                    logical_model_id=context.logical_model_id,
                    provider_model_id=provider.id,
                    protocol=ProviderProtocol.OPENAI,
                    call_type="completion",
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    success=False,
                    latency_ms=latency_ms,
                    request_payload=payload,
                    response_body=None,
                    error_message=str(exc),
                    request_logging_enabled=context.request_logging_enabled,
                    response_logging_enabled=context.response_logging_enabled,
                    usage=None,
                    provider=provider,
                )
            )
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

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
            usage=usage_data,
            provider_prices=prices_data,
        )
