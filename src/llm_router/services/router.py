from __future__ import annotations

import random
from datetime import date
from decimal import Decimal
from typing import Sequence

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from llm_router.core.config import get_settings
from llm_router.core.security import Encryptor, hash_api_key
from llm_router.domain.enums import ProviderProtocol
from llm_router.domain.models import ApiKey, LogicalModel, LogicalModelRoute
from llm_router.domain.schemas import (
    CachedApiKey,
    CachedProvider,
    CachedRoute,
    RequestContext,
    RoutableProvider,
    RoutableProviderGroup,
    RoutedProvider,
)
from llm_router.services.billing import check_balance_and_budget
from llm_router.services.cache.degraded_cache import DegradedRouteCache, DegradedType
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
        try:
            _check_balance_from_cache(cached)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)) from exc

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


def weighted_random_select(
    candidates: Sequence[RoutableProvider],
) -> RoutableProvider | None:
    """
    加权随机选择

    Args:
        candidates: 可选候选列表（weight > 0）

    Returns:
        选中的候选，或 None（无可用候选）
    """
    if not candidates:
        return None

    # 过滤 weight > 0 的候选
    valid_candidates = [c for c in candidates if c.weight > 0]
    if not valid_candidates:
        return None

    # 计算总权重
    total_weight = sum(c.weight for c in valid_candidates)
    if total_weight <= 0:
        return None

    # 随机选择
    r = random.randint(0, total_weight - 1)
    cumulative = 0
    for candidate in valid_candidates:
        cumulative += candidate.weight
        if r < cumulative:
            return candidate

    # 理论上不会走到这里，但作为保底返回最后一个
    return valid_candidates[-1]


async def resolve_provider_candidates(
    session: AsyncSession,
    logical_model_id: int,
    protocol: ProviderProtocol,
) -> list[RoutableProviderGroup]:
    """
    解析可用的 provider 候选列表，按优先级分组

    路由流程：
    1. 获取所有 active 且 weight > 0 的路由
    2. 过滤掉 degraded 状态的路由
    3. 按 is_fallback 和 priority 分组
    4. 组按 priority 排序，fallback 组在最后

    Returns:
        list[RoutableProviderGroup]，按 priority 升序排列
    """
    dual_cache = get_dual_cache()
    degraded_cache = DegradedRouteCache(dual_cache) if dual_cache else None

    # === 1. 获取路由数据 ===
    cached_routes_data = None
    if dual_cache:
        cached_routes_data = await dual_cache.get_routes_by_logical_model(logical_model_id)

    if cached_routes_data:
        # 缓存命中
        routes_data = cached_routes_data
    else:
        # 缓存 miss：从 DB 查询
        stmt = (
            select(LogicalModelRoute)
            .options(joinedload(LogicalModelRoute.provider_model))
            .where(
                LogicalModelRoute.logical_model_id == logical_model_id,
                LogicalModelRoute.status == "active",
            )
            .order_by(LogicalModelRoute.priority.asc(), LogicalModelRoute.id.asc())
        )
        db_routes = (await session.execute(stmt)).scalars().all()

        routes_data = []
        routes_to_cache: list[dict] = []

        for route in db_routes:
            provider = route.provider_model
            if not provider.is_active:
                continue

            # Check if provider has an endpoint for the requested protocol
            _ep = provider.openai_endpoint if protocol == ProviderProtocol.OPENAI else provider.anthropic_endpoint
            if not _ep:
                continue

            # 构建缓存数据
            cached_route = CachedRoute(
                route_id=route.id,
                logical_model_id=route.logical_model_id,
                provider_model_id=provider.id,
                priority=route.priority,
                weight=route.weight,
                is_fallback=route.is_fallback,
                status=route.status,
            )
            routes_data.append(cached_route.to_dict())

            if dual_cache:
                routes_to_cache.append(cached_route.to_dict())

                cached_provider = CachedProvider(
                    id=provider.id,
                    name=provider.name,
                    openai_endpoint=provider.openai_endpoint,
                    anthropic_endpoint=provider.anthropic_endpoint,
                    encrypted_api_key=provider.encrypted_api_key,
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

    # === 2. 解析路由并构建 provider ===
    all_routable: list[tuple[int, int, bool, int, RoutedProvider]] = []  # (route_id, priority, is_fallback, weight, provider)

    for route_data in routes_data:
        route = CachedRoute.from_dict(route_data)

        # 过滤 weight <= 0
        if route.weight <= 0:
            continue

        # 检查 degraded 状态
        if degraded_cache:
            degraded_status = await degraded_cache.get_status(route.route_id)
            if degraded_status is not None:
                # 路由已降级，跳过
                continue

        # 获取 provider 数据
        cached_provider_data = None
        if dual_cache:
            cached_provider_data = await dual_cache.get_provider(route.provider_model_id)

        if not cached_provider_data:
            continue

        provider = CachedProvider.from_dict(cached_provider_data)
        if not provider.is_active:
            continue

        # Resolve endpoint for requested protocol
        resolved_endpoint = provider.openai_endpoint if protocol == ProviderProtocol.OPENAI else provider.anthropic_endpoint
        if not resolved_endpoint:
            continue

        routed_provider = RoutedProvider(
            id=provider.id,
            name=provider.name,
            protocol=protocol,
            endpoint=resolved_endpoint,
            api_key=encryptor.decrypt(provider.encrypted_api_key),
            upstream_model_name=provider.upstream_model_name,
            timeout_seconds=provider.timeout_seconds,
            input_token_price=provider.input_token_price,
            output_token_price=provider.output_token_price,
            cache_read_token_price=provider.cache_read_token_price,
            cache_write_token_price=provider.cache_write_token_price,
            supports_prompt_cache=provider.supports_prompt_cache,
        )

        all_routable.append((route.route_id, route.priority, route.is_fallback, route.weight, routed_provider))

    # === 3. 按 is_fallback 和 priority 分组 ===
    main_groups: dict[int, list[RoutableProvider]] = {}  # priority -> providers
    fallback_groups: dict[int, list[RoutableProvider]] = {}

    for route_id, priority, is_fallback, weight, provider in all_routable:
        routable = RoutableProvider(
            route_id=route_id,
            provider=provider,
            weight=weight,
        )
        if is_fallback:
            if priority not in fallback_groups:
                fallback_groups[priority] = []
            fallback_groups[priority].append(routable)
        else:
            if priority not in main_groups:
                main_groups[priority] = []
            main_groups[priority].append(routable)

    # === 4. 构建返回列表 ===
    result: list[RoutableProviderGroup] = []

    # 先添加 main 组，按 priority 升序
    for priority in sorted(main_groups.keys()):
        result.append(RoutableProviderGroup(
            priority=priority,
            is_fallback=False,
            providers=main_groups[priority],
        ))

    # 再添加 fallback 组
    for priority in sorted(fallback_groups.keys()):
        result.append(RoutableProviderGroup(
            priority=priority,
            is_fallback=True,
            providers=fallback_groups[priority],
        ))

    if not result:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No provider available")

    return result
