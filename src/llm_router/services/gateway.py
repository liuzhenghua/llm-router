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
from llm_router.domain.models import ApiKey, RequestLog
from llm_router.domain.schemas import RequestContext, RoutedProvider, UsageSnapshot
from llm_router.services.billing import record_billing
from llm_router.services.router import resolve_provider_candidates, resolve_request_context


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


async def _create_request_log(
    session: AsyncSession,
    *,
    context: RequestContext,
    provider: RoutedProvider,
    status_code: int | None = None,
    success: bool = False,
    latency_ms: int | None = None,
    request_body: str | None = None,
    response_body: str | None = None,
    error_message: str | None = None,
    upstream_request_id: str | None = None,
) -> RequestLog:
    request_log = RequestLog(
        request_id=context.request_id,
        api_key_id=context.api_key_id,
        logical_model_id=context.logical_model_id,
        provider_model_id=provider.id,
        protocol=context.protocol.value,
        upstream_request_id=upstream_request_id,
        status_code=status_code,
        success=success,
        latency_ms=latency_ms,
        request_body=request_body,
        response_body=response_body,
        error_message=error_message,
    )
    session.add(request_log)
    await session.flush()
    return request_log


async def _finalize_success(
    session: AsyncSession,
    *,
    api_key: ApiKey,
    context: RequestContext,
    provider: RoutedProvider,
    usage: UsageSnapshot,
    status_code: int,
    latency_ms: int,
    request_payload: dict[str, Any],
    response_body: str | None,
    upstream_request_id: str | None,
) -> None:
    request_log = await _create_request_log(
        session,
        context=context,
        provider=provider,
        status_code=status_code,
        success=True,
        latency_ms=latency_ms,
        request_body=json.dumps(request_payload, ensure_ascii=False) if context.request_logging_enabled else None,
        response_body=response_body if context.response_logging_enabled else None,
        upstream_request_id=upstream_request_id,
    )
    await record_billing(
        session,
        api_key=api_key,
        request_log=request_log,
        provider=provider,
        usage=usage,
    )
    await session.commit()


async def _finalize_failure(
    session: AsyncSession,
    *,
    context: RequestContext,
    provider: RoutedProvider,
    status_code: int,
    latency_ms: int,
    request_payload: dict[str, Any],
    error_message: str,
    upstream_request_id: str | None = None,
) -> None:
    await _create_request_log(
        session,
        context=context,
        provider=provider,
        status_code=status_code,
        success=False,
        latency_ms=latency_ms,
        request_body=json.dumps(request_payload, ensure_ascii=False) if context.request_logging_enabled else None,
        error_message=error_message,
        upstream_request_id=upstream_request_id,
    )
    await session.commit()


async def _proxy_non_stream(
    session: AsyncSession,
    *,
    api_key: ApiKey,
    context: RequestContext,
    provider: RoutedProvider,
) -> JSONResponse:
    payload = _prepare_payload(context.payload, provider, context.protocol)
    headers = _build_upstream_headers(provider, context, context.protocol)
    usage = UsageSnapshot()
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=provider.timeout_seconds) as client:
            response = await client.post(provider.endpoint, json=payload, headers=headers)
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.text)
        body = response.json()
        if context.protocol == ProviderProtocol.OPENAI:
            _extract_openai_usage(body, usage)
        else:
            _extract_anthropic_usage(body, usage)
        latency_ms = int((time.perf_counter() - started) * 1000)
        await _finalize_success(
            session,
            api_key=api_key,
            context=context,
            provider=provider,
            usage=usage,
            status_code=response.status_code,
            latency_ms=latency_ms,
            request_payload=payload,
            response_body=json.dumps(body, ensure_ascii=False),
            upstream_request_id=response.headers.get("x-request-id") or response.headers.get("request-id"),
        )
        return JSONResponse(content=body, status_code=response.status_code, headers=_filter_headers(response.headers))
    except HTTPException as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        await _finalize_failure(
            session,
            context=context,
            provider=provider,
            status_code=exc.status_code,
            latency_ms=latency_ms,
            request_payload=payload,
            error_message=str(exc.detail),
        )
        raise
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        await _finalize_failure(
            session,
            context=context,
            provider=provider,
            status_code=status.HTTP_502_BAD_GATEWAY,
            latency_ms=latency_ms,
            request_payload=payload,
            error_message=str(exc),
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
) -> StreamingResponse:
    payload = _prepare_payload(context.payload, provider, context.protocol)
    headers = _build_upstream_headers(provider, context, context.protocol)
    usage = UsageSnapshot()
    started = time.perf_counter()
    accumulated: list[str] = []
    client = httpx.AsyncClient(timeout=provider.timeout_seconds)
    stream_cm = client.stream("POST", provider.endpoint, json=payload, headers=headers)
    upstream_response = await stream_cm.__aenter__()
    upstream_request_id = upstream_response.headers.get("x-request-id") or upstream_response.headers.get("request-id")

    if upstream_response.status_code >= 400:
        detail = (await upstream_response.aread()).decode("utf-8")
        await stream_cm.__aexit__(None, None, None)
        await client.aclose()
        raise HTTPException(status_code=upstream_response.status_code, detail=detail)

    async def event_iterator():
        stream_failed = False
        error_message = ""
        try:
            current_event: str | None = None
            async for line in upstream_response.aiter_lines():
                raw = f"{line}\n"
                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_part = line.split(":", 1)[1].strip()
                    if data_part and data_part != "[DONE]":
                        try:
                            data = json.loads(data_part)
                        except json.JSONDecodeError:
                            data = None
                        if data is not None:
                            delta = _extract_stream_delta(context.protocol, current_event, data, usage)
                            if delta and context.response_logging_enabled:
                                accumulated.append(delta)
                elif line == "":
                    current_event = None
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
            try:
                if stream_failed:
                    await _finalize_failure(
                        session,
                        context=context,
                        provider=provider,
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        latency_ms=latency_ms,
                        request_payload=payload,
                        error_message=error_message or "Streaming failed",
                        upstream_request_id=upstream_request_id,
                    )
                else:
                    await _finalize_success(
                        session,
                        api_key=api_key,
                        context=context,
                        provider=provider,
                        usage=usage,
                        status_code=upstream_response.status_code,
                        latency_ms=latency_ms,
                        request_payload=payload,
                        response_body="".join(accumulated) if accumulated else None,
                        upstream_request_id=upstream_request_id,
                    )
            except Exception:
                await session.rollback()
            finally:
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
                return await _proxy_stream(session, api_key=api_key, context=context, provider=provider)
            return await _proxy_non_stream(session, api_key=api_key, context=context, provider=provider)
        except HTTPException as exc:
            last_error = exc
            if exc.status_code < 500:
                break
            await session.rollback()
            continue
    if last_error is not None:
        raise last_error
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No provider succeeded")
