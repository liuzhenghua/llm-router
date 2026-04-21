from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_router.domain.enums import ChangeType
from llm_router.domain.models import ApiKey, BalanceLedger, DailyUsageSummary, RequestLog, UsageRecord
from llm_router.domain.schemas import BillingResult, RoutedProvider, UsageSnapshot
from llm_router.services.cache.spend_queue import SpendDelta, get_spend_queue


ZERO = Decimal("0")
PER_MILLION = Decimal("1000000")


def _per_million_cost(tokens: int, unit_price: Decimal) -> Decimal:
    return (Decimal(tokens) / PER_MILLION) * unit_price


def compute_costs(provider: RoutedProvider, usage: UsageSnapshot) -> BillingResult:
    # 注意: prompt_tokens 包含 cache_read_tokens 和 cache_write_tokens，需减去避免重复计费
    non_cache_prompt_tokens = max(0, usage.prompt_tokens - usage.cache_read_tokens - usage.cache_write_tokens)
    cost_input = _per_million_cost(non_cache_prompt_tokens, provider.input_token_price)
    cost_output = _per_million_cost(usage.completion_tokens, provider.output_token_price)
    cost_cache_read = _per_million_cost(usage.cache_read_tokens, provider.cache_read_token_price)
    cost_cache_write = _per_million_cost(usage.cache_write_tokens, provider.cache_write_token_price)
    total = cost_input + cost_output + cost_cache_read + cost_cache_write
    return BillingResult(
        total_cost=total,
        cost_input=cost_input,
        cost_output=cost_output,
        cost_cache_read=cost_cache_read,
        cost_cache_write=cost_cache_write,
    )


async def check_balance_and_budget(api_key: ApiKey) -> None:
    today = date.today()
    if api_key.daily_spend_date != today:
        api_key.daily_spend_date = today
        api_key.daily_spend_amount = ZERO
    if api_key.balance <= ZERO:
        raise ValueError("Insufficient balance")
    if api_key.daily_budget_limit is not None and api_key.daily_spend_amount >= api_key.daily_budget_limit:
        raise ValueError("Daily budget exhausted")


async def record_billing(
    session: AsyncSession,
    *,
    api_key: ApiKey,
    request_log: RequestLog,
    provider: RoutedProvider,
    usage: UsageSnapshot,
) -> BillingResult:
    """
    记录计费

    注意：balance 和 daily_spend_amount 的更新通过增量队列异步写入 DB，
    以支持多 Pod 并发写入时不会互相覆盖。其他记录（UsageRecord, BalanceLedger,
    DailyUsageSummary）仍然立即写入。
    """
    today = date.today()
    if api_key.daily_spend_date != today:
        api_key.daily_spend_date = today
        api_key.daily_spend_amount = ZERO

    costs = compute_costs(provider, usage)
    if api_key.daily_budget_limit is not None and api_key.daily_spend_amount + costs.total_cost > api_key.daily_budget_limit:
        raise ValueError("Daily budget would be exceeded")
    if api_key.balance - costs.total_cost < ZERO:
        raise ValueError("Insufficient balance")

    # === 增量队列：推送 balance 和 daily_spend_amount 的更新 ===
    spend_queue = get_spend_queue()
    delta_amount = -costs.total_cost  # 负数表示扣款

    if spend_queue:
        delta = SpendDelta(
            api_key_id=api_key.id,
            delta_amount=delta_amount,
            request_id=request_log.request_id,
        )
        await spend_queue.push(delta)
    else:
        # Fallback: 直接更新（仅用于无队列的简单场景）
        api_key.balance = Decimal(api_key.balance) + delta_amount
        api_key.daily_spend_amount = Decimal(api_key.daily_spend_amount) + costs.total_cost

    # === 立即写入：UsageRecord, BalanceLedger, DailyUsageSummary ===
    balance_before = Decimal(api_key.balance)

    summary_stmt = select(DailyUsageSummary).where(
        DailyUsageSummary.api_key_id == api_key.id,
        DailyUsageSummary.summary_date == today,
    )
    summary = (await session.execute(summary_stmt)).scalar_one_or_none()
    if summary is None:
        summary = DailyUsageSummary(
            api_key_id=api_key.id,
            summary_date=today,
        )
        session.add(summary)

    summary.request_count += 1
    summary.prompt_tokens += usage.prompt_tokens
    summary.completion_tokens += usage.completion_tokens
    summary.cache_read_tokens += usage.cache_read_tokens
    summary.cache_write_tokens += usage.cache_write_tokens
    summary.cost_total = Decimal(summary.cost_total) + costs.total_cost

    usage_record = UsageRecord(
        request_log_id=request_log.id,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        input_token_price_snapshot=provider.input_token_price,
        output_token_price_snapshot=provider.output_token_price,
        cache_read_price_snapshot=provider.cache_read_token_price,
        cache_write_price_snapshot=provider.cache_write_token_price,
        cost_input=costs.cost_input,
        cost_output=costs.cost_output,
        cost_cache_read=costs.cost_cache_read,
        cost_cache_write=costs.cost_cache_write,
        cost_total=costs.total_cost,
        currency="USD",
        billing_date=today,
    )
    session.add(usage_record)
    session.add(
        BalanceLedger(
            api_key_id=api_key.id,
            change_type=ChangeType.CHARGE.value,
            amount=-costs.total_cost,
            balance_before=balance_before,
            balance_after=balance_before + delta_amount,  # 使用预估的 after 值
            reference_type="request_log",
            reference_id=str(request_log.id),
            remark=f"Charged for {request_log.request_id}",
        )
    )
    return costs
