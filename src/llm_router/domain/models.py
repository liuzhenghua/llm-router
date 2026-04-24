from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from llm_router.core.config import get_settings

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from llm_router.core.database import Base, table_name


class JsonString(TypeDecorator):
    """Stores a JSON-serialisable value as TEXT.

    This avoids any dependency on MySQL's native JSON type, making the schema
    compatible with MySQL < 5.7.8, MariaDB, and SQLite without any code changes
    in the rest of the application.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return json.loads(value)


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
    __tablename__ = table_name("api_keys")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    encrypted_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"))
    daily_budget_limit: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    daily_spend_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"))
    daily_spend_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    qps_limit: Mapped[int] = mapped_column(Integer, default=5)
    allowed_logical_models_json: Mapped[list[str]] = mapped_column(JsonString, default=list)
    request_content_logging_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    response_content_logging_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    end_user: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # IANA timezone name (e.g. "UTC", "Asia/Shanghai") — used for billing date calculation
    timezone: Mapped[str] = mapped_column(String(64), default=lambda: get_settings().tz)
    # Default channel tag — used as fallback when x-channel request header is not provided
    default_channel: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    request_logs: Mapped[list["RequestLog"]] = relationship(
        "RequestLog",
        primaryjoin="ApiKey.id == RequestLog.api_key_id",
        foreign_keys="[RequestLog.api_key_id]",
        back_populates="api_key",
    )


class LogicalModel(Base, TimestampMixin):
    __tablename__ = table_name("logical_models")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    routing_strategy: Mapped[str] = mapped_column(String(32), default="priority")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    routes: Mapped[list["LogicalModelRoute"]] = relationship(
        "LogicalModelRoute",
        primaryjoin="LogicalModel.id == LogicalModelRoute.logical_model_id",
        foreign_keys="[LogicalModelRoute.logical_model_id]",
        back_populates="logical_model",
    )


class ProviderModel(Base, TimestampMixin):
    __tablename__ = table_name("provider_models")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    openai_endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    anthropic_endpoint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    upstream_model_name: Mapped[str] = mapped_column(String(120))
    input_token_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    output_token_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    supports_prompt_cache: Mapped[bool] = mapped_column(Boolean, default=False)
    cache_read_token_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    cache_write_token_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=120)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)

    routes: Mapped[list["LogicalModelRoute"]] = relationship(
        "LogicalModelRoute",
        primaryjoin="ProviderModel.id == LogicalModelRoute.provider_model_id",
        foreign_keys="[LogicalModelRoute.provider_model_id]",
        back_populates="provider_model",
    )


class LogicalModelRoute(Base):
    __tablename__ = table_name("logical_model_routes")
    __table_args__ = (
        UniqueConstraint("logical_model_id", "provider_model_id", name="uq_logical_provider_route"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    logical_model_id: Mapped[int] = mapped_column(Integer, index=True)
    provider_model_id: Mapped[int] = mapped_column(Integer, index=True)
    # 优先级：数值越小越优先，按 priority 分组后按权重分配流量
    priority: Mapped[int] = mapped_column(Integer, default=100)
    # 权重：同组内按权重加权随机分配流量，weight=0 表示不参与路由
    weight: Mapped[int] = mapped_column(Integer, default=1)
    # 是否为后备路由：true=后备路由（主路由全部失败才调用），false=主路由
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="active")

    logical_model: Mapped["LogicalModel"] = relationship(
        "LogicalModel",
        primaryjoin="LogicalModelRoute.logical_model_id == LogicalModel.id",
        foreign_keys="[LogicalModelRoute.logical_model_id]",
        back_populates="routes",
    )
    provider_model: Mapped["ProviderModel"] = relationship(
        "ProviderModel",
        primaryjoin="LogicalModelRoute.provider_model_id == ProviderModel.id",
        foreign_keys="[LogicalModelRoute.provider_model_id]",
        back_populates="routes",
    )


class RequestLog(Base):
    __tablename__ = table_name("request_logs")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    api_key_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    logical_model_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    provider_model_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    protocol: Mapped[str] = mapped_column(String(32))
    call_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    upstream_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_user: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    channel: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=func.now())

    api_key: Mapped["ApiKey | None"] = relationship(
        "ApiKey",
        primaryjoin="RequestLog.api_key_id == ApiKey.id",
        foreign_keys="[RequestLog.api_key_id]",
        back_populates="request_logs",
    )
    provider_model: Mapped["ProviderModel | None"] = relationship(
        "ProviderModel",
        primaryjoin="RequestLog.provider_model_id == ProviderModel.id",
        foreign_keys="[RequestLog.provider_model_id]",
    )
    usage_record: Mapped["UsageRecord | None"] = relationship(
        "UsageRecord",
        primaryjoin="RequestLog.id == UsageRecord.request_log_id",
        foreign_keys="[UsageRecord.request_log_id]",
        back_populates="request_log",
        uselist=False,
    )
    body: Mapped["RequestLogBody | None"] = relationship(
        "RequestLogBody",
        primaryjoin="RequestLog.id == RequestLogBody.request_log_id",
        foreign_keys="[RequestLogBody.request_log_id]",
        back_populates="request_log",
        uselist=False,
    )


class RequestLogBody(Base):
    __tablename__ = table_name("request_log_bodies")

    request_log_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)

    request_log: Mapped["RequestLog"] = relationship(
        "RequestLog",
        primaryjoin="RequestLogBody.request_log_id == RequestLog.id",
        foreign_keys="[RequestLogBody.request_log_id]",
        back_populates="body",
    )


class UsageRecord(Base):
    __tablename__ = table_name("usage_records")

    request_log_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)
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
    billing_date: Mapped[date] = mapped_column(Date, index=True)

    request_log: Mapped["RequestLog"] = relationship(
        "RequestLog",
        primaryjoin="UsageRecord.request_log_id == RequestLog.id",
        foreign_keys="[UsageRecord.request_log_id]",
        back_populates="usage_record",
    )


class BalanceLedger(Base):
    __tablename__ = table_name("balance_ledgers")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_key_id: Mapped[int] = mapped_column(Integer, index=True)
    change_type: Mapped[str] = mapped_column(String(32))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    balance_before: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    reference_type: Mapped[str] = mapped_column(String(32))
    reference_id: Mapped[str] = mapped_column(String(64))
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=func.now(), index=True)


class DailyUsageSummary(Base):
    __tablename__ = table_name("daily_usage_summaries")
    __table_args__ = (
        UniqueConstraint("api_key_id", "summary_date", name="uq_api_key_summary_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_key_id: Mapped[int] = mapped_column(Integer)
    summary_date: Mapped[date] = mapped_column(Date)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_total: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
    )


class AdminUser(Base, TimestampMixin):
    __tablename__ = table_name("admin_users")

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
