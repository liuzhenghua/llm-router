from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_router.core.database import SessionLocal
from llm_router.domain.enums import ChangeType
from llm_router.domain.models import ApiKey, BalanceLedger, DailyUsageSummary, RequestLog, UsageRecord

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
PER_MILLION = Decimal("1000000")


@dataclass(slots=True, frozen=True)
class UsageSnapshotData:
    """Usage 数据快照 - 无 ORM 依赖"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(slots=True, frozen=True)
class ProviderPricesData:
    """Provider 价格快照 - 无 ORM 依赖"""
    input_token_price: Decimal
    output_token_price: Decimal
    cache_read_token_price: Decimal
    cache_write_token_price: Decimal


@dataclass(slots=True, frozen=True)
class RequestFinalizationData:
    """请求结束后日志和计费需要的所有数据"""
    request_id: str
    upstream_request_id: str | None
    api_key_id: int
    logical_model_id: int
    provider_model_id: int
    protocol: str
    call_type: str
    status_code: int
    success: bool
    latency_ms: int
    request_body: str | None
    response_body: str | None
    error_message: str | None
    started_at: datetime | None
    ended_at: datetime | None
    usage: UsageSnapshotData | None
    provider_prices: ProviderPricesData | None


def _per_million_cost(tokens: int, unit_price: Decimal) -> Decimal:
    return (Decimal(tokens) / PER_MILLION) * unit_price


async def _create_request_log_task(
    session: AsyncSession,
    data: RequestFinalizationData,
) -> RequestLog | None:
    """异步创建或更新请求日志"""
    # 检查是否已存在（处理重试场景）
    stmt = select(RequestLog).where(RequestLog.request_id == data.request_id)
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()
    if existing:
        # 重试场景：更新已有日志
        existing.status_code = data.status_code
        existing.success = data.success
        existing.latency_ms = data.latency_ms
        existing.response_body = data.response_body
        existing.error_message = data.error_message
        existing.call_type = data.call_type
        # upstream_request_id 可能在重试成功时才获取到
        if data.upstream_request_id:
            existing.upstream_request_id = data.upstream_request_id
        existing.ended_at = data.ended_at
        await session.flush()
        return existing

    request_log = RequestLog(
        request_id=data.request_id,
        api_key_id=data.api_key_id,
        logical_model_id=data.logical_model_id,
        provider_model_id=data.provider_model_id,
        protocol=data.protocol,
        call_type=data.call_type,
        upstream_request_id=data.upstream_request_id,
        status_code=data.status_code,
        success=data.success,
        latency_ms=data.latency_ms,
        request_body=data.request_body,
        response_body=data.response_body,
        error_message=data.error_message,
        started_at=data.started_at,
        ended_at=data.ended_at,
    )
    session.add(request_log)
    await session.flush()
    return request_log


async def _record_billing_task(
    session: AsyncSession,
    request_log: RequestLog,
    data: RequestFinalizationData,
) -> None:
    """异步执行计费逻辑"""
    today = date.today()
    usage = data.usage
    prices = data.provider_prices

    # 获取并锁定 ApiKey（使用 FOR UPDATE）
    stmt = select(ApiKey).where(ApiKey.id == data.api_key_id).with_for_update()
    result = await session.execute(stmt)
    api_key = result.scalar_one_or_none()
    if api_key is None:
        logger.warning(f"ApiKey not found for billing: {data.api_key_id}")
        return

    # 重置每日限额
    if api_key.daily_spend_date != today:
        api_key.daily_spend_date = today
        api_key.daily_spend_amount = ZERO

    # 计算费用
    if usage and prices:
        cost_input = _per_million_cost(usage.prompt_tokens, prices.input_token_price)
        cost_output = _per_million_cost(usage.completion_tokens, prices.output_token_price)
        cost_cache_read = _per_million_cost(usage.cache_read_tokens, prices.cache_read_token_price)
        cost_cache_write = _per_million_cost(usage.cache_write_tokens, prices.cache_write_token_price)
        total_cost = cost_input + cost_output + cost_cache_read + cost_cache_write
    else:
        total_cost = ZERO
        cost_input = cost_output = cost_cache_read = cost_cache_write = ZERO

    # 检查余额
    if api_key.balance - total_cost < ZERO:
        logger.warning(f"Insufficient balance for request {data.request_id}: {api_key.balance} - {total_cost}")

    # 更新余额
    balance_before = Decimal(api_key.balance)
    api_key.balance = balance_before - total_cost
    api_key.daily_spend_amount = Decimal(api_key.daily_spend_amount) + total_cost
    api_key.daily_spend_date = today

    # 更新或创建 DailyUsageSummary
    summary_stmt = select(DailyUsageSummary).where(
        DailyUsageSummary.api_key_id == api_key.id,
        DailyUsageSummary.summary_date == today,
    ).with_for_update()
    summary = (await session.execute(summary_stmt)).scalar_one_or_none()
    if summary is None:
        summary = DailyUsageSummary(
            api_key_id=api_key.id,
            summary_date=today,
            request_count=0,
            prompt_tokens=0,
            completion_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost_total=Decimal("0"),
        )
        session.add(summary)

    summary.request_count += 1
    if usage:
        summary.prompt_tokens += usage.prompt_tokens
        summary.completion_tokens += usage.completion_tokens
        summary.cache_read_tokens += usage.cache_read_tokens
        summary.cache_write_tokens += usage.cache_write_tokens
    summary.cost_total = Decimal(summary.cost_total) + total_cost

    # 创建 UsageRecord
    if usage and prices:
        usage_record = UsageRecord(
            request_log_id=request_log.id,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            input_token_price_snapshot=prices.input_token_price,
            output_token_price_snapshot=prices.output_token_price,
            cache_read_price_snapshot=prices.cache_read_token_price,
            cache_write_price_snapshot=prices.cache_write_token_price,
            cost_input=cost_input,
            cost_output=cost_output,
            cost_cache_read=cost_cache_read,
            cost_cache_write=cost_cache_write,
            cost_total=total_cost,
            currency="USD",
            billing_date=today,
        )
        session.add(usage_record)

    # 创建 BalanceLedger
    session.add(
        BalanceLedger(
            api_key_id=api_key.id,
            change_type=ChangeType.CHARGE.value,
            amount=-total_cost,
            balance_before=balance_before,
            balance_after=api_key.balance,
            reference_type="request_log",
            reference_id=str(request_log.id),
            remark=f"Charged for {request_log.request_id}",
        )
    )


async def _execute_post_request_tasks(data: RequestFinalizationData) -> None:
    """执行所有后置任务（在新 session 中运行）"""
    async with SessionLocal() as session:
        try:
            # 1. 创建请求日志
            request_log = await _create_request_log_task(session, data)

            # 2. 如果成功且有计费信息，执行计费
            if data.success and data.usage and data.provider_prices:
                await _record_billing_task(session, request_log, data)

            # 3. 提交事务
            await session.commit()

        except Exception as exc:
            logger.error(f"Post-request task failed for {data.request_id}: {exc}", exc_info=True)
            await session.rollback()


def schedule_post_request_tasks(data: RequestFinalizationData) -> None:
    """安排后置任务（不阻塞当前请求）"""
    asyncio.create_task(_execute_post_request_tasks(data))
