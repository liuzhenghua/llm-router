from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from llm_router.domain.enums import ProviderProtocol
from llm_router.domain.models import ApiKey
from llm_router.domain.schemas import RequestContext, RoutedProvider
from llm_router.services.non_stream_handlers import (
    AnthropicNonStreamHandler,
    OpenAINonStreamHandler,
)
from llm_router.services.router import resolve_provider_candidates, resolve_request_context
from llm_router.services.streaming_handlers import (
    AnthropicStreamingHandler,
    OpenAIStreamingHandler,
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
                if context.protocol == ProviderProtocol.OPENAI:
                    handler = OpenAIStreamingHandler()
                else:
                    handler = AnthropicStreamingHandler()
                return await handler.proxy(
                    session,
                    api_key=api_key,
                    context=context,
                    provider=provider,
                    request_path=request_path,
                )
            else:
                if context.protocol == ProviderProtocol.OPENAI:
                    handler = OpenAINonStreamHandler()
                else:
                    handler = AnthropicNonStreamHandler()
                return await handler.proxy(
                    session,
                    api_key=api_key,
                    context=context,
                    provider=provider,
                    request_path=request_path,
                )
        except HTTPException as exc:
            last_error = exc
            if exc.status_code < 500:
                break
            await session.rollback()
            continue
    if last_error is not None:
        raise last_error
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No provider succeeded")
