from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from llm_router.core.database import Base


def utcnow() -> datetime:
    return datetime.utcnow()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"))
    daily_budget_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    daily_spend_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"))
    daily_spend_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    qps_limit: Mapped[int] = mapped_column(Integer, default=5)
    allowed_logical_models_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    request_content_logging_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    response_content_logging_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    request_logs: Mapped[list["RequestLog"]] = relationship(back_populates="api_key")


class LogicalModel(Base, TimestampMixin):
    __tablename__ = "logical_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    routing_strategy: Mapped[str] = mapped_column(String(32), default="priority")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    routes: Mapped[list["LogicalModelRoute"]] = relationship(back_populates="logical_model")


class ProviderModel(Base, TimestampMixin):
    __tablename__ = "provider_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    endpoint: Mapped[str] = mapped_column(String(255))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    protocol: Mapped[str] = mapped_column(String(32), index=True)
    upstream_model_name: Mapped[str] = mapped_column(String(120))
    input_token_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    output_token_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    supports_prompt_cache: Mapped[bool] = mapped_column(Boolean, default=False)
    cache_read_token_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    cache_write_token_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=120)

    routes: Mapped[list["LogicalModelRoute"]] = relationship(back_populates="provider_model")


class LogicalModelRoute(Base):
    __tablename__ = "logical_model_routes"
    __table_args__ = (
        UniqueConstraint("logical_model_id", "provider_model_id", name="uq_logical_provider_route"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    logical_model_id: Mapped[int] = mapped_column(ForeignKey("logical_models.id", ondelete="CASCADE"))
    provider_model_id: Mapped[int] = mapped_column(ForeignKey("provider_models.id", ondelete="CASCADE"))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    weight: Mapped[int] = mapped_column(Integer, default=1)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="active")

    logical_model: Mapped[LogicalModel] = relationship(back_populates="routes")
    provider_model: Mapped[ProviderModel] = relationship(back_populates="routes")


class RequestLog(Base):
    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    api_key_id: Mapped[int | None] = mapped_column(ForeignKey("api_keys.id"), nullable=True)
    logical_model_id: Mapped[int | None] = mapped_column(ForeignKey("logical_models.id"), nullable=True)
    provider_model_id: Mapped[int | None] = mapped_column(ForeignKey("provider_models.id"), nullable=True)
    protocol: Mapped[str] = mapped_column(String(32))
    call_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    upstream_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=func.now())

    api_key: Mapped[ApiKey | None] = relationship(back_populates="request_logs")
    usage_record: Mapped["UsageRecord | None"] = relationship(back_populates="request_log", uselist=False)


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_log_id: Mapped[int] = mapped_column(ForeignKey("request_logs.id", ondelete="CASCADE"), unique=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    input_token_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    output_token_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    cache_read_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    cache_write_price_snapshot: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    cost_input: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    cost_output: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    cost_cache_read: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    cost_cache_write: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    cost_total: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    billing_date: Mapped[date] = mapped_column(Date, default=date.today)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=func.now())

    request_log: Mapped[RequestLog] = relationship(back_populates="usage_record")


class BalanceLedger(Base):
    __tablename__ = "balance_ledgers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id", ondelete="CASCADE"))
    change_type: Mapped[str] = mapped_column(String(32))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    balance_before: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    reference_type: Mapped[str] = mapped_column(String(32))
    reference_id: Mapped[str] = mapped_column(String(64))
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=func.now())


class DailyUsageSummary(Base):
    __tablename__ = "daily_usage_summaries"
    __table_args__ = (
        UniqueConstraint("api_key_id", "summary_date", name="uq_api_key_summary_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id", ondelete="CASCADE"))
    summary_date: Mapped[date] = mapped_column(Date, default=date.today)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_total: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )
