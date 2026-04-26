from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from llm_router.domain.enums import ProviderProtocol
from llm_router.domain.models import ApiKey
from llm_router.domain.schemas import RequestContext, RoutableProvider, RoutableProviderGroup, RoutedProvider
from llm_router.services.cache.degraded_cache import DegradedRouteCache, DegradedType
from llm_router.services.cache.dual_cache import get_dual_cache
from llm_router.services.non_stream_handlers import (
    AnthropicNonStreamHandler,
    OpenAIEmbeddingNonStreamHandler,
    OpenAINonStreamHandler,
)
from llm_router.services.non_stream_handlers.cross_protocol import (
    AnthropicOverOpenAINonStreamHandler,
    OpenAIOverAnthropicNonStreamHandler,
)
from llm_router.services.router import resolve_provider_candidates, resolve_request_context, weighted_random_select
from llm_router.services.streaming_handlers import (
    AnthropicStreamingHandler,
    OpenAIStreamingHandler,
)
from llm_router.services.streaming_handlers.cross_protocol import (
    AnthropicOverOpenAIStreamingHandler,
    OpenAIOverAnthropicStreamingHandler,
)


logger = logging.getLogger(__name__)

# 组内最大重试次数
MAX_RETRY_PER_GROUP = 3


async def handle_proxy_request(
    session: AsyncSession,
    *,
    protocol: ProviderProtocol,
    payload: dict[str, Any],
    raw_api_key: str,
    headers: dict[str, str],
    request_path: str,
    request: Request | None = None,
) -> JSONResponse | StreamingResponse:
    logical_model_name = payload.get("model")
    if not logical_model_name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="model is required")
    stream = bool(payload.get("stream", False))
    request_id = headers.get("x-request-id") or uuid.uuid4().hex
    headers = {**headers, "x-request-id": request_id}

    api_key, context = await resolve_request_context(
        session,
        raw_api_key=raw_api_key,
        logical_model_name=logical_model_name,
        protocol=protocol,
        payload=payload,
        stream=stream,
        headers=headers,
    )
    context.request_id = request_id

    # Expose resolved context fields to access-log middleware via request.state
    if request is not None:
        request.state.access_log_context = (
            f"{context.api_key_name or '-'}|{context.channel or '-'}|{context.end_user or '-'}"
        )

    # 获取分组后的 provider 候选列表
    provider_groups = await resolve_provider_candidates(session, context.logical_model_ids, protocol)

    # All DB queries for this request are done. Release the connection back to the pool
    # before blocking on the upstream LLM call. Streaming responses can take 30–300 s;
    # holding an idle MySQL connection for that duration risks CR_SERVER_LOST (2013).
    # The session remains usable — it will lazily acquire a new connection if needed.
    await session.close()

    # 初始化降级缓存
    dual_cache = get_dual_cache()
    degraded_cache = DegradedRouteCache(dual_cache) if dual_cache else None

    last_error: HTTPException | None = None

    # 遍历各组：先 Main 组，再 Fallback 组
    for group in provider_groups:
        # 在组内加权随机选择 provider
        selected = weighted_random_select(group.providers)
        if selected is None:
            # 组内没有可用 provider，切换下一组
            continue

        # 记录当前选中的 provider 和 route_id
        current_provider = selected.provider
        current_route_id = selected.route_id
        context.logical_model_id = selected.logical_model_id

        # 组内重试逻辑
        for attempt in range(MAX_RETRY_PER_GROUP):
            try:
                if context.stream:
                    if context.protocol == ProviderProtocol.OPENAI:
                        if current_provider.upstream_protocol == ProviderProtocol.OPENAI:
                            handler = OpenAIStreamingHandler()
                        else:
                            handler = OpenAIOverAnthropicStreamingHandler()
                    else:
                        if current_provider.upstream_protocol == ProviderProtocol.ANTHROPIC:
                            handler = AnthropicStreamingHandler()
                        else:
                            handler = AnthropicOverOpenAIStreamingHandler()
                    return await handler.proxy(
                        session,
                        api_key=api_key,
                        context=context,
                        provider=current_provider,
                        request_path=request_path,
                    )
                else:
                    if context.protocol == ProviderProtocol.OPENAI:
                        if current_provider.upstream_protocol == ProviderProtocol.OPENAI:
                            handler = OpenAINonStreamHandler()
                        else:
                            handler = OpenAIOverAnthropicNonStreamHandler()
                    else:
                        if current_provider.upstream_protocol == ProviderProtocol.ANTHROPIC:
                            handler = AnthropicNonStreamHandler()
                        else:
                            handler = AnthropicOverOpenAINonStreamHandler()
                    return await handler.proxy(
                        session,
                        api_key=api_key,
                        context=context,
                        provider=current_provider,
                        request_path=request_path,
                    )
            except HTTPException as exc:
                last_error = exc

                # 401/402/403/429：上游 provider 自身问题（认证失败/余额不足/权限不足/限流），
                # 标记当前 route 为 degraded，然后在组内重试其他 provider
                if exc.status_code in (401, 402, 403, 429):
                    if degraded_cache:
                        await degraded_cache.mark_degraded(
                            route_id=current_route_id,
                            degraded_type=DegradedType.QUOTA_EXHAUSTED,
                            fail_count=DegradedRouteCache.FAIL_COUNT_THRESHOLD,
                        )

                # 4xx 其他错误（除 401/402/403/429）：直接返回，不重试不降级
                elif 400 <= exc.status_code < 500:
                    raise exc

                # 5xx：连通性错误，记录失败次数
                else:
                    if degraded_cache:
                        new_count = await degraded_cache.increment_fail_count(current_route_id)
                        if new_count >= DegradedRouteCache.FAIL_COUNT_THRESHOLD:
                            # 失败次数超限，标记为 degraded (unavailable)
                            await degraded_cache.mark_degraded(
                                route_id=current_route_id,
                                degraded_type=DegradedType.UNAVAILABLE,
                                fail_count=new_count,
                            )
                            logger.warning(
                                f"Route {current_route_id} marked as unavailable after {new_count} failures"
                            )

                # 重试：重新在组内选择其他 provider（当前 route 已被标记为 degraded，不会被选中）
                if attempt < MAX_RETRY_PER_GROUP - 1:
                    selected = weighted_random_select(group.providers)
                    if selected:
                        current_provider = selected.provider
                        current_route_id = selected.route_id
                        context.logical_model_id = selected.logical_model_id
                        continue
                    # 组内没有其他可用 provider，跳出重试循环，切换下一组
                break

        # 当前组全部失败，切换下一组
        continue

    # 所有组都失败了
    if last_error is not None:
        raise last_error
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No provider succeeded")


async def handle_embedding_request(
    session: AsyncSession,
    *,
    payload: dict[str, Any],
    raw_api_key: str,
    headers: dict[str, str],
    request: Request | None = None,
) -> JSONResponse:
    """Handle OpenAI-compatible embeddings requests.

    Embeddings are always non-streaming and require an OpenAI-compatible upstream.
    Providers with only an Anthropic endpoint are skipped.
    """
    logical_model_name = payload.get("model")
    if not logical_model_name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="model is required")

    request_id = headers.get("x-request-id") or uuid.uuid4().hex
    headers = {**headers, "x-request-id": request_id}

    api_key, context = await resolve_request_context(
        session,
        raw_api_key=raw_api_key,
        logical_model_name=logical_model_name,
        protocol=ProviderProtocol.OPENAI,
        payload=payload,
        stream=False,
        headers=headers,
    )
    context.request_id = request_id

    # Expose resolved context fields to access-log middleware via request.state
    if request is not None:
        request.state.access_log_context = (
            f"{context.api_key_name or '-'}|{context.channel or '-'}|{context.end_user or '-'}"
        )

    provider_groups = await resolve_provider_candidates(session, context.logical_model_ids, ProviderProtocol.OPENAI)

    # All DB queries for this request are done. Release the connection back to the pool
    # before blocking on the upstream embedding call.
    await session.close()

    dual_cache = get_dual_cache()
    degraded_cache = DegradedRouteCache(dual_cache) if dual_cache else None

    last_error: HTTPException | None = None

    for group in provider_groups:
        # Embeddings only work with OpenAI-compatible upstream endpoints
        openai_providers = [p for p in group.providers if p.provider.upstream_protocol == ProviderProtocol.OPENAI]
        if not openai_providers:
            continue

        selected = weighted_random_select(openai_providers)
        if selected is None:
            continue

        current_provider = selected.provider
        current_route_id = selected.route_id
        context.logical_model_id = selected.logical_model_id

        for attempt in range(MAX_RETRY_PER_GROUP):
            try:
                handler = OpenAIEmbeddingNonStreamHandler()
                return await handler.proxy(
                    session,
                    api_key=api_key,
                    context=context,
                    provider=current_provider,
                    request_path="/embeddings",
                )
            except HTTPException as exc:
                last_error = exc

                # 401/402/403/429：上游 provider 自身问题（认证失败/余额不足/权限不足/限流），
                # 标记当前 route 为 degraded，然后在组内重试其他 provider
                if exc.status_code in (401, 402, 403, 429):
                    if degraded_cache:
                        await degraded_cache.mark_degraded(
                            route_id=current_route_id,
                            degraded_type=DegradedType.QUOTA_EXHAUSTED,
                            fail_count=DegradedRouteCache.FAIL_COUNT_THRESHOLD,
                        )

                # 4xx 其他错误（除 401/402/403/429）：直接返回，不重试不降级
                elif 400 <= exc.status_code < 500:
                    raise exc

                # 5xx：连通性错误，记录失败次数
                else:
                    if degraded_cache:
                        new_count = await degraded_cache.increment_fail_count(current_route_id)
                        if new_count >= DegradedRouteCache.FAIL_COUNT_THRESHOLD:
                            await degraded_cache.mark_degraded(
                                route_id=current_route_id,
                                degraded_type=DegradedType.UNAVAILABLE,
                                fail_count=new_count,
                            )
                            logger.warning(
                                f"Route {current_route_id} marked as unavailable after {new_count} failures"
                            )

                # 重试：重新在组内选择其他 provider（当前 route 已被标记为 degraded，不会被选中）
                if attempt < MAX_RETRY_PER_GROUP - 1:
                    selected = weighted_random_select(openai_providers)
                    if selected:
                        current_provider = selected.provider
                        current_route_id = selected.route_id
                        context.logical_model_id = selected.logical_model_id
                        continue
                    # 组内没有其他可用 provider，跳出重试循环，切换下一组
                break

        # 当前组全部失败，切换下一组
        continue

    if last_error is not None:
        raise last_error
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No provider available for embeddings")
