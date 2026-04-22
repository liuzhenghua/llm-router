"""Cross-protocol non-streaming handlers.

AnthropicOverOpenAINonStreamHandler:
    Client speaks Anthropic → converts request to OpenAI → calls OpenAI upstream →
    converts response back to Anthropic → returns Anthropic response.

OpenAIOverAnthropicNonStreamHandler:
    Client speaks OpenAI → converts request to Anthropic → calls Anthropic upstream →
    converts response back to OpenAI → returns OpenAI response.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx
from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from llm_router.domain.enums import ProviderProtocol
from llm_router.domain.models import utcnow
from llm_router.domain.schemas import UsageSnapshot
from llm_router.services.non_stream_handlers.base import BaseNonStreamHandler
from llm_router.services.post_request import RequestFinalizationData, schedule_post_request_tasks
from llm_router.services.protocol_converter import (
    anthropic_to_openai_request,
    anthropic_to_openai_response,
    get_usage_from_anthropic_response,
    get_usage_from_openai_response,
    openai_to_anthropic_request,
    openai_to_anthropic_response,
)

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
    )


class AnthropicOverOpenAINonStreamHandler(BaseNonStreamHandler):
    """Anthropic client → OpenAI upstream (non-streaming).

    The client request is in Anthropic format. We convert it to OpenAI, call the
    OpenAI endpoint, then convert the response back to Anthropic format.
    """

    _UPSTREAM_PATH = "/chat/completions"

    def prepare_payload(self, payload: dict, provider: Any) -> dict:
        return anthropic_to_openai_request(payload, provider.upstream_model_name)

    def build_upstream_headers(self, provider: Any, context: Any) -> dict:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "authorization": f"Bearer {provider.api_key}",
        }

    def get_usage(self, body: dict) -> UsageSnapshot | None:
        return get_usage_from_openai_response(body)

    def get_upstream_request_id(self, body: dict, headers: httpx.Headers) -> str | None:
        if body.get("id"):
            return body["id"]
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
        started_at = utcnow()
        full_endpoint = provider.endpoint.rstrip("/") + self._UPSTREAM_PATH

        try:
            async with httpx.AsyncClient(timeout=provider.timeout_seconds) as client:
                response = await client.post(full_endpoint, json=payload, headers=headers)

            if response.status_code >= 400:
                raise HTTPException(status_code=response.status_code, detail=response.text)

            upstream_body = response.json()
            usage = self.get_usage(upstream_body)
            upstream_request_id = self.get_upstream_request_id(upstream_body, response.headers)
            latency_ms = int((time.perf_counter() - started) * 1000)

            # Convert OpenAI response → Anthropic response
            anthropic_body = openai_to_anthropic_response(upstream_body, context.logical_model_name)

            schedule_post_request_tasks(
                _build_finalization_data(
                    request_id=context.request_id,
                    upstream_request_id=upstream_request_id,
                    api_key_id=context.api_key_id,
                    logical_model_id=context.logical_model_id,
                    provider_model_id=provider.id,
                    protocol=ProviderProtocol.ANTHROPIC,
                    call_type="completion",
                    status_code=response.status_code,
                    success=True,
                    latency_ms=latency_ms,
                    request_payload=payload,
                    response_body=json.dumps(anthropic_body, ensure_ascii=False),
                    error_message=None,
                    request_logging_enabled=context.request_logging_enabled,
                    response_logging_enabled=context.response_logging_enabled,
                    usage=usage,
                    provider=provider,
                    started_at=started_at,
                    ended_at=utcnow(),
                    end_user=context.end_user,
                )
            )

            return JSONResponse(
                content=anthropic_body,
                status_code=response.status_code,
                headers=_filter_headers(response.headers),
            )

        except HTTPException:
            raise
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            schedule_post_request_tasks(
                _build_finalization_data(
                    request_id=context.request_id,
                    upstream_request_id=None,
                    api_key_id=context.api_key_id,
                    logical_model_id=context.logical_model_id,
                    provider_model_id=provider.id,
                    protocol=ProviderProtocol.ANTHROPIC,
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
                    started_at=started_at,
                    ended_at=utcnow(),
                    end_user=context.end_user,
                )
            )
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


class OpenAIOverAnthropicNonStreamHandler(BaseNonStreamHandler):
    """OpenAI client → Anthropic upstream (non-streaming).

    The client request is in OpenAI format. We convert it to Anthropic, call the
    Anthropic endpoint, then convert the response back to OpenAI format.
    """

    _UPSTREAM_PATH = "/v1/messages"

    def prepare_payload(self, payload: dict, provider: Any) -> dict:
        return openai_to_anthropic_request(payload, provider.upstream_model_name)

    def build_upstream_headers(self, provider: Any, context: Any) -> dict:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "x-api-key": provider.api_key,
            "anthropic-version": "2023-06-01",
        }

    def get_usage(self, body: dict) -> UsageSnapshot | None:
        return get_usage_from_anthropic_response(body)

    def get_upstream_request_id(self, body: dict, headers: httpx.Headers) -> str | None:
        if body.get("id"):
            return body["id"]
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
        started_at = utcnow()
        full_endpoint = provider.endpoint.rstrip("/") + self._UPSTREAM_PATH

        try:
            async with httpx.AsyncClient(timeout=provider.timeout_seconds) as client:
                response = await client.post(full_endpoint, json=payload, headers=headers)

            if response.status_code >= 400:
                raise HTTPException(status_code=response.status_code, detail=response.text)

            upstream_body = response.json()
            usage = self.get_usage(upstream_body)
            upstream_request_id = self.get_upstream_request_id(upstream_body, response.headers)
            latency_ms = int((time.perf_counter() - started) * 1000)

            # Convert Anthropic response → OpenAI response
            openai_body = anthropic_to_openai_response(upstream_body)

            schedule_post_request_tasks(
                _build_finalization_data(
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
                    response_body=json.dumps(openai_body, ensure_ascii=False),
                    error_message=None,
                    request_logging_enabled=context.request_logging_enabled,
                    response_logging_enabled=context.response_logging_enabled,
                    usage=usage,
                    provider=provider,
                    started_at=started_at,
                    ended_at=utcnow(),
                    end_user=context.end_user,
                )
            )

            return JSONResponse(
                content=openai_body,
                status_code=response.status_code,
                headers=_filter_headers(response.headers),
            )

        except HTTPException:
            raise
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            schedule_post_request_tasks(
                _build_finalization_data(
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
                    started_at=started_at,
                    ended_at=utcnow(),
                    end_user=context.end_user,
                )
            )
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
