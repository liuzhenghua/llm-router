from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import HTTPException, status
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

    # 获取分组后的 provider 候选列表
    provider_groups = await resolve_provider_candidates(session, logical_model.id, protocol)

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

                # 429/403：立即标记为 degraded (quota_exhausted)
                if exc.status_code == 429 or exc.status_code == 403:
                    if degraded_cache:
                        await degraded_cache.mark_degraded(
                            route_id=current_route_id,
                            degraded_type=DegradedType.QUOTA_EXHAUSTED,
                            fail_count=DegradedRouteCache.FAIL_COUNT_THRESHOLD,
                        )
                    # 直接切换下一组，不再重试
                    break

                # 4xx 其他错误（除 429/403）：直接返回，不重试不降级
                if 400 <= exc.status_code < 500:
                    raise exc

                # 5xx：错误，记录并重试同组下一个
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

                await session.rollback()

                # 重试：重新在组内选择（可能选到同一个，也可能选到其他）
                if attempt < MAX_RETRY_PER_GROUP - 1:
                    selected = weighted_random_select(group.providers)
                    if selected:
                        current_provider = selected.provider
                        current_route_id = selected.route_id
                continue

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

    api_key, logical_model, context = await resolve_request_context(
        session,
        raw_api_key=raw_api_key,
        logical_model_name=logical_model_name,
        protocol=ProviderProtocol.OPENAI,
        payload=payload,
        stream=False,
        headers=headers,
    )
    context.request_id = request_id

    provider_groups = await resolve_provider_candidates(session, logical_model.id, ProviderProtocol.OPENAI)

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

                if exc.status_code == 429 or exc.status_code == 403:
                    if degraded_cache:
                        await degraded_cache.mark_degraded(
                            route_id=current_route_id,
                            degraded_type=DegradedType.QUOTA_EXHAUSTED,
                            fail_count=DegradedRouteCache.FAIL_COUNT_THRESHOLD,
                        )
                    break

                if 400 <= exc.status_code < 500:
                    raise exc

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

                await session.rollback()

                if attempt < MAX_RETRY_PER_GROUP - 1:
                    selected = weighted_random_select(openai_providers)
                    if selected:
                        current_provider = selected.provider
                        current_route_id = selected.route_id
                continue

        continue

    if last_error is not None:
        raise last_error
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No provider available for embeddings")
