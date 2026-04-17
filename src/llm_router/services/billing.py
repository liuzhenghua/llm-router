from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_router.domain.enums import ChangeType
from llm_router.domain.models import ApiKey, BalanceLedger, DailyUsageSummary, RequestLog, UsageRecord
from llm_router.domain.schemas import BillingResult, RoutedProvider, UsageSnapshot


ZERO = Decimal("0")
PER_MILLION = Decimal("1000000")


def _per_million_cost(tokens: int, unit_price: Decimal) -> Decimal:
    return (Decimal(tokens) / PER_MILLION) * unit_price


def compute_costs(provider: RoutedProvider, usage: UsageSnapshot) -> BillingResult:
    cost_input = _per_million_cost(usage.prompt_tokens, provider.input_token_price)
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
    today = date.today()
    if api_key.daily_spend_date != today:
        api_key.daily_spend_date = today
        api_key.daily_spend_amount = ZERO

    costs = compute_costs(provider, usage)
    if api_key.daily_budget_limit is not None and api_key.daily_spend_amount + costs.total_cost > api_key.daily_budget_limit:
        raise ValueError("Daily budget would be exceeded")
    if api_key.balance - costs.total_cost < ZERO:
        raise ValueError("Insufficient balance")

    balance_before = Decimal(api_key.balance)
    api_key.balance = balance_before - costs.total_cost
    api_key.daily_spend_amount = Decimal(api_key.daily_spend_amount) + costs.total_cost
    api_key.daily_spend_date = today

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
            balance_after=api_key.balance,
            reference_type="request_log",
            reference_id=str(request_log.id),
            remark=f"Charged for {request_log.request_id}",
        )
    )
    return costs
