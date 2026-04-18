from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx
from fastapi import HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from llm_router.domain.enums import ProviderProtocol
from llm_router.domain.models import ApiKey
from llm_router.domain.schemas import RequestContext, RoutedProvider, UsageSnapshot
from llm_router.services.post_request import (
    ProviderPricesData,
    RequestFinalizationData,
    UsageSnapshotData,
    schedule_post_request_tasks,
)
from llm_router.services.router import resolve_provider_candidates, resolve_request_context
from llm_router.services.streaming_handlers import (
    AnthropicStreamingHandler,
    BaseStreamingHandler,
    OpenAIStreamingHandler,
)


HOP_BY_HOP_HEADERS = {
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


def _filter_headers(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP_HEADERS}


def _extract_openai_usage(payload: dict[str, Any], usage: UsageSnapshot) -> None:
    usage_obj = payload.get("usage") or {}
    usage.prompt_tokens = usage_obj.get("prompt_tokens", usage.prompt_tokens)
    usage.completion_tokens = usage_obj.get("completion_tokens", usage.completion_tokens)
    details = usage_obj.get("prompt_tokens_details") or {}
    usage.cache_read_tokens = details.get("cached_tokens", usage.cache_read_tokens)


def _extract_anthropic_usage(payload: dict[str, Any], usage: UsageSnapshot) -> None:
    usage_obj = payload.get("usage") or payload.get("message", {}).get("usage") or {}
    usage.prompt_tokens = usage_obj.get("input_tokens", usage.prompt_tokens)
    usage.completion_tokens = usage_obj.get("output_tokens", usage.completion_tokens)
    usage.cache_read_tokens = usage_obj.get("cache_read_input_tokens", usage.cache_read_tokens)
    usage.cache_write_tokens = usage_obj.get("cache_creation_input_tokens", usage.cache_write_tokens)


def _build_upstream_headers(provider: RoutedProvider, context: RequestContext, protocol: ProviderProtocol) -> dict[str, str]:
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
    }
    if protocol == ProviderProtocol.OPENAI:
        headers["authorization"] = f"Bearer {provider.api_key}"
    else:
        headers["x-api-key"] = provider.api_key
        headers["anthropic-version"] = context.headers.get("anthropic-version", "2023-06-01")
        if beta := context.headers.get("anthropic-beta"):
            headers["anthropic-beta"] = beta
    return headers


def _prepare_payload(payload: dict[str, Any], provider: RoutedProvider, protocol: ProviderProtocol) -> dict[str, Any]:
    patched = json.loads(json.dumps(payload))
    patched["model"] = provider.upstream_model_name
    if protocol == ProviderProtocol.OPENAI and patched.get("stream"):
        stream_options = patched.setdefault("stream_options", {})
        stream_options["include_usage"] = True
    return patched


def _create_finalization_data(
    *,
    request_id: str,
    upstream_request_id: str | None,
    api_key_id: int,
    logical_model_id: int,
    provider_model_id: int,
    protocol: ProviderProtocol,
    status_code: int,
    success: bool,
    latency_ms: int,
    request_payload: dict[str, Any] | None,
    response_body: str | None,
    error_message: str | None,
    request_logging_enabled: bool,
    response_logging_enabled: bool,
    usage: UsageSnapshot | None,
    provider: RoutedProvider,
) -> RequestFinalizationData:
    """创建后置任务数据对象"""
    # 序列化请求体（如果启用）
    request_body = None
    if request_logging_enabled and request_payload:
        try:
            request_body = json.dumps(request_payload, ensure_ascii=False)
        except Exception:
            pass

    # 序列化响应体（如果启用）
    response_body_serialized = response_body if response_logging_enabled else None

    # 转换 usage 为纯数据对象
    usage_data = None
    if usage:
        usage_data = UsageSnapshotData(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
        )

    # 转换 provider 价格
    prices_data = ProviderPricesData(
        input_token_price=provider.input_token_price,
        output_token_price=provider.output_token_price,
        cache_read_token_price=provider.cache_read_token_price,
        cache_write_token_price=provider.cache_write_token_price,
    )

    return RequestFinalizationData(
        request_id=request_id,
        upstream_request_id=upstream_request_id,
        api_key_id=api_key_id,
        logical_model_id=logical_model_id,
        provider_model_id=provider.id,
        protocol=protocol.value,
        status_code=status_code,
        success=success,
        latency_ms=latency_ms,
        request_body=request_body,
        response_body=response_body_serialized,
        error_message=error_message,
        usage=usage_data,
        provider_prices=prices_data,
    )




async def _proxy_non_stream(
    session: AsyncSession,
    *,
    api_key: ApiKey,
    context: RequestContext,
    provider: RoutedProvider,
    request_path: str,
) -> JSONResponse:
    payload = _prepare_payload(context.payload, provider, context.protocol)
    headers = _build_upstream_headers(provider, context, context.protocol)
    usage = UsageSnapshot()
    started = time.perf_counter()
    full_endpoint = provider.endpoint.rstrip("/") + request_path
    try:
        async with httpx.AsyncClient(timeout=provider.timeout_seconds) as client:
            response = await client.post(full_endpoint, json=payload, headers=headers)
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.text)
        body = response.json()
        if context.protocol == ProviderProtocol.OPENAI:
            _extract_openai_usage(body, usage)
        else:
            _extract_anthropic_usage(body, usage)
        latency_ms = int((time.perf_counter() - started) * 1000)
        schedule_post_request_tasks(
            _create_finalization_data(
                request_id=context.request_id,
                upstream_request_id=response.headers.get("x-request-id") or response.headers.get("request-id"),
                api_key_id=context.api_key_id,
                logical_model_id=context.logical_model_id,
                provider_model_id=provider.id,
                protocol=context.protocol,
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
        return JSONResponse(content=body, status_code=response.status_code, headers=_filter_headers(response.headers))
    except HTTPException as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        schedule_post_request_tasks(
            _create_finalization_data(
                request_id=context.request_id,
                upstream_request_id=None,
                api_key_id=context.api_key_id,
                logical_model_id=context.logical_model_id,
                provider_model_id=provider.id,
                protocol=context.protocol,
                status_code=exc.status_code,
                success=False,
                latency_ms=latency_ms,
                request_payload=payload,
                response_body=None,
                error_message=str(exc.detail),
                request_logging_enabled=context.request_logging_enabled,
                response_logging_enabled=context.response_logging_enabled,
                usage=None,
                provider=provider,
            )
        )
        raise
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        schedule_post_request_tasks(
            _create_finalization_data(
                request_id=context.request_id,
                upstream_request_id=None,
                api_key_id=context.api_key_id,
                logical_model_id=context.logical_model_id,
                provider_model_id=provider.id,
                protocol=context.protocol,
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


def _extract_stream_delta(protocol: ProviderProtocol, event_name: str | None, data: dict[str, Any], usage: UsageSnapshot) -> str | None:
    if protocol == ProviderProtocol.OPENAI:
        _extract_openai_usage(data, usage)
        for choice in data.get("choices", []):
            delta = choice.get("delta") or {}
            if content := delta.get("content"):
                return str(content)
        return None

    if event_name == "message_start":
        _extract_anthropic_usage(data, usage)
    elif event_name == "message_delta":
        _extract_anthropic_usage(data, usage)
    elif event_name == "content_block_delta":
        delta = data.get("delta") or {}
        return delta.get("text")
    return None


async def _proxy_stream(
    session: AsyncSession,
    *,
    api_key: ApiKey,
    context: RequestContext,
    provider: RoutedProvider,
    request_path: str,
) -> StreamingResponse:
    payload = _prepare_payload(context.payload, provider, context.protocol)
    headers = _build_upstream_headers(provider, context, context.protocol)
    started = time.perf_counter()

    # 根据协议选择流式处理器
    if context.protocol == ProviderProtocol.OPENAI:
        stream_handler: BaseStreamingHandler = OpenAIStreamingHandler()
    else:
        stream_handler = AnthropicStreamingHandler()

    full_endpoint = provider.endpoint.rstrip("/") + request_path
    client = httpx.AsyncClient(timeout=provider.timeout_seconds)
    stream_cm = client.stream("POST", full_endpoint, json=payload, headers=headers)
    upstream_response = await stream_cm.__aenter__()

    if upstream_response.status_code >= 400:
        detail = (await upstream_response.aread()).decode("utf-8")
        await stream_cm.__aexit__(None, None, None)
        await client.aclose()
        raise HTTPException(status_code=upstream_response.status_code, detail=detail)

    async def event_iterator():
        stream_failed = False
        error_message = ""
        try:
            async for line in upstream_response.aiter_lines():
                raw = f"{line}\n"
                # 使用流式处理器处理每一行
                await stream_handler.process_line(line)
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
            latency_ms = int((time.perf_counter() - started) * 1000)
            # 使用流式处理器获取合并后的响应和 usage
            response_body = stream_handler.get_accumulated_response() if not stream_failed else None
            usage = stream_handler.get_usage()

            schedule_post_request_tasks(
                _create_finalization_data(
                    request_id=context.request_id,
                    upstream_request_id=stream_handler.get_upstream_request_id(),
                    api_key_id=context.api_key_id,
                    logical_model_id=context.logical_model_id,
                    provider_model_id=provider.id,
                    protocol=context.protocol,
                    status_code=upstream_response.status_code if not stream_failed else status.HTTP_502_BAD_GATEWAY,
                    success=not stream_failed,
                    latency_ms=latency_ms,
                    request_payload=payload,
                    response_body=response_body,
                    error_message=error_message or None,
                    request_logging_enabled=context.request_logging_enabled,
                    response_logging_enabled=context.response_logging_enabled,
                    usage=usage,
                    provider=provider,
                )
            )
            await stream_cm.__aexit__(None, None, None)
            await client.aclose()

    return StreamingResponse(
        event_iterator(),
        media_type="text/event-stream",
        headers=_filter_headers(upstream_response.headers),
    )


async def handle_proxy_request(
    session: AsyncSession,
    *,
    protocol: ProviderProtocol,
    payload: dict[str, Any],
    raw_api_key: str,
    headers: dict[str, str],
    request_path: str,
) -> JSONResponse | StreamingResponse:
    logical_model_name = payload.get("model")
    if not logical_model_name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="model is required")
    stream = bool(payload.get("stream", False))
    request_id = headers.get("x-request-id") or uuid.uuid4().hex
    headers = {**headers, "x-request-id": request_id}

    api_key, logical_model, context = await resolve_request_context(
        session,
        raw_api_key=raw_api_key,
        logical_model_name=logical_model_name,
        protocol=protocol,
        payload=payload,
        stream=stream,
        headers=headers,
    )
    context.request_id = request_id

    providers = await resolve_provider_candidates(session, logical_model.id, protocol)
    last_error: HTTPException | None = None
    for provider in providers:
        try:
            if context.stream:
                return await _proxy_stream(session, api_key=api_key, context=context, provider=provider, request_path=request_path)
            return await _proxy_non_stream(session, api_key=api_key, context=context, provider=provider, request_path=request_path)
        except HTTPException as exc:
            last_error = exc
            if exc.status_code < 500:
                break
            await session.rollback()
            continue
    if last_error is not None:
        raise last_error
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No provider succeeded")
