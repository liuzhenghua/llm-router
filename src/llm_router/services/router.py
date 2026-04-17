from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from llm_router.core.config import get_settings
from llm_router.core.security import Encryptor
from llm_router.domain.enums import ProviderProtocol
from llm_router.domain.models import ApiKey, LogicalModel, LogicalModelRoute
from llm_router.domain.schemas import RequestContext, RoutedProvider
from llm_router.services.billing import check_balance_and_budget
from llm_router.services.rate_limit import rate_limiter


settings = get_settings()
encryptor = Encryptor(settings.app_encryption_key)


async def resolve_request_context(
    session: AsyncSession,
    *,
    raw_api_key: str,
    logical_model_name: str,
    protocol: ProviderProtocol,
    payload: dict,
    stream: bool,
    headers: dict[str, str],
) -> tuple[ApiKey, LogicalModel, RequestContext]:
    from llm_router.core.security import hash_api_key

    api_key = (
        await session.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw_api_key), ApiKey.status == "active")
        )
    ).scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    await rate_limiter.check(api_key.id, api_key.qps_limit)
    try:
        await check_balance_and_budget(api_key)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)) from exc

    logical_model = (
        await session.execute(select(LogicalModel).where(LogicalModel.name == logical_model_name, LogicalModel.is_active))
    ).scalar_one_or_none()
    if logical_model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Logical model not found")

    if api_key.allowed_logical_models_json and logical_model_name not in api_key.allowed_logical_models_json:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Model not allowed for API key")

    context = RequestContext(
        request_id=headers.get("x-request-id", ""),
        protocol=protocol,
        logical_model_name=logical_model_name,
        payload=payload,
        stream=stream,
        request_logging_enabled=api_key.request_content_logging_enabled or settings.default_request_logging_enabled,
        response_logging_enabled=api_key.response_content_logging_enabled or settings.default_response_logging_enabled,
        api_key_id=api_key.id,
        api_key_name=api_key.name,
        logical_model_id=logical_model.id,
        raw_authorization=raw_api_key,
        headers=headers,
    )
    return api_key, logical_model, context


async def resolve_provider_candidates(
    session: AsyncSession,
    logical_model_id: int,
    protocol: ProviderProtocol,
) -> list[RoutedProvider]:
    stmt = (
        select(LogicalModelRoute)
        .options(joinedload(LogicalModelRoute.provider_model))
        .where(
            LogicalModelRoute.logical_model_id == logical_model_id,
            LogicalModelRoute.status == "active",
        )
        .order_by(LogicalModelRoute.priority.asc(), LogicalModelRoute.id.asc())
    )
    routes = (await session.execute(stmt)).scalars().all()
    providers: list[RoutedProvider] = []
    for route in routes:
        provider = route.provider_model
        if not provider.is_active or provider.protocol != protocol.value:
            continue
        providers.append(
            RoutedProvider(
                id=provider.id,
                name=provider.name,
                protocol=protocol,
                endpoint=provider.endpoint,
                api_key=encryptor.decrypt(provider.encrypted_api_key),
                upstream_model_name=provider.upstream_model_name,
                timeout_seconds=provider.timeout_seconds,
                input_token_price=provider.input_token_price,
                output_token_price=provider.output_token_price,
                cache_read_token_price=provider.cache_read_token_price,
                cache_write_token_price=provider.cache_write_token_price,
                supports_prompt_cache=provider.supports_prompt_cache,
            )
        )
    if not providers:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No provider available")
    return providers
