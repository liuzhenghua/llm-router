from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from llm_router.core.config import get_settings
from llm_router.core.security import Encryptor, hash_api_key
from llm_router.domain.enums import ProviderProtocol
from llm_router.domain.models import ApiKey, LogicalModel, LogicalModelRoute
from llm_router.domain.schemas import CachedApiKey, CachedProvider, CachedRoute, RequestContext, RoutedProvider
from llm_router.services.billing import check_balance_and_budget
from llm_router.services.cache.dual_cache import get_dual_cache
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
    key_hash = hash_api_key(raw_api_key)
    dual_cache = get_dual_cache()

    # === 1. 尝试从缓存获取 ApiKey ===
    cached_apikey_data = None
    if dual_cache:
        cached_apikey_data = await dual_cache.get_apikey_by_hash(key_hash)

    if cached_apikey_data:
        # 缓存命中：从缓存数据重建 ApiKey 对象（仅包含鉴权所需字段）
        cached = CachedApiKey.from_dict(cached_apikey_data)

        # 验证状态
        if cached.status != "active":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key is not active")

        # 限流检查
        await rate_limiter.check(cached.id, cached.qps_limit)

        # 余额检查（使用缓存的余额）
        _check_balance_from_cache(cached)

        # 检查模型权限
        if cached.allowed_logical_models_json and logical_model_name not in cached.allowed_logical_models_json:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Model not allowed for API key")

        # 查 LogicalModel（暂不缓存）
        logical_model = (
            await session.execute(select(LogicalModel).where(LogicalModel.name == logical_model_name, LogicalModel.is_active))
        ).scalar_one_or_none()
        if logical_model is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Logical model not found")

        # 从缓存数据创建轻量级 ApiKey 对象（用于返回）
        api_key = ApiKey(
            id=cached.id,
            name=cached.name,
            key_hash=key_hash,
            status=cached.status,
            balance=cached.balance,
            daily_budget_limit=cached.daily_budget_limit,
            daily_spend_amount=cached.daily_spend_amount,
            daily_spend_date=date.fromisoformat(cached.daily_spend_date) if cached.daily_spend_date else None,
            qps_limit=cached.qps_limit,
            allowed_logical_models_json=cached.allowed_logical_models_json,
            request_content_logging_enabled=False,
            response_content_logging_enabled=False,
        )

        context = RequestContext(
            request_id=headers.get("x-request-id", ""),
            protocol=protocol,
            logical_model_name=logical_model_name,
            payload=payload,
            stream=stream,
            request_logging_enabled=settings.default_request_logging_enabled,
            response_logging_enabled=settings.default_response_logging_enabled,
            api_key_id=api_key.id,
            api_key_name=api_key.name,
            logical_model_id=logical_model.id,
            raw_authorization=raw_api_key,
            headers=headers,
        )
        return api_key, logical_model, context

    # === 2. 缓存 miss：从 DB 查询 ===
    api_key = (
        await session.execute(
            select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.status == "active")
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

    # === 3. 回填缓存 ===
    if dual_cache:
        cached_api_key = CachedApiKey(
            id=api_key.id,
            name=api_key.name,
            status=api_key.status,
            balance=api_key.balance,
            daily_budget_limit=api_key.daily_budget_limit,
            daily_spend_amount=api_key.daily_spend_amount,
            daily_spend_date=api_key.daily_spend_date.isoformat() if api_key.daily_spend_date else None,
            qps_limit=api_key.qps_limit,
            allowed_logical_models_json=api_key.allowed_logical_models_json or [],
        )
        await dual_cache.set_apikey(key_hash, cached_api_key.to_dict())

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


def _check_balance_from_cache(cached: CachedApiKey) -> None:
    """从缓存数据检查余额和预算（不访问 DB）"""
    if cached.balance <= 0:
        raise ValueError("Insufficient balance")

    today = date.today().isoformat()
    if cached.daily_spend_date != today:
        # 新的一天，重置日预算
        return

    if cached.daily_budget_limit is not None and cached.daily_spend_amount >= cached.daily_budget_limit:
        raise ValueError("Daily budget limit exceeded")


async def resolve_provider_candidates(
    session: AsyncSession,
    logical_model_id: int,
    protocol: ProviderProtocol,
) -> list[RoutedProvider]:
    dual_cache = get_dual_cache()

    # === 1. 尝试从缓存获取路由 ===
    if dual_cache:
        cached_routes_data = await dual_cache.get_routes_by_logical_model(logical_model_id)
        if cached_routes_data:
            providers: list[RoutedProvider] = []
            for route_data in cached_routes_data:
                route = CachedRoute.from_dict(route_data)
                if route.status != "active":
                    continue

                cached_provider_data = await dual_cache.get_provider(route.provider_model_id)
                if not cached_provider_data:
                    continue

                provider = CachedProvider.from_dict(cached_provider_data)
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

            if providers:
                return providers

    # === 2. 缓存 miss：从 DB 查询 ===
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
    routes_to_cache: list[dict] = []

    for route in routes:
        provider = route.provider_model
        if not provider.is_active or provider.protocol != protocol.value:
            continue

        routed = RoutedProvider(
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
        providers.append(routed)

        # 缓存数据
        if dual_cache:
            cached_route = CachedRoute(
                route_id=route.id,
                logical_model_id=route.logical_model_id,
                provider_model_id=provider.id,
                priority=route.priority,
                weight=route.weight,
                is_fallback=route.is_fallback,
                status=route.status,
            )
            routes_to_cache.append(cached_route.to_dict())

            cached_provider = CachedProvider(
                id=provider.id,
                name=provider.name,
                endpoint=provider.endpoint,
                encrypted_api_key=provider.encrypted_api_key,
                protocol=provider.protocol,
                upstream_model_name=provider.upstream_model_name,
                input_token_price=provider.input_token_price,
                output_token_price=provider.output_token_price,
                cache_read_token_price=provider.cache_read_token_price,
                cache_write_token_price=provider.cache_write_token_price,
                supports_prompt_cache=provider.supports_prompt_cache,
                timeout_seconds=provider.timeout_seconds,
                is_active=provider.is_active,
            )
            await dual_cache.set_provider(provider.id, cached_provider.to_dict())

    # 回填路由列表缓存
    if dual_cache and routes_to_cache:
        await dual_cache.set_routes(logical_model_id, routes_to_cache)

    if not providers:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No provider available")

    return providers
