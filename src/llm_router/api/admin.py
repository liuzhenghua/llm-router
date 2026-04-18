from __future__ import annotations

from decimal import Decimal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import desc, or_, select
from sqlalchemy.orm import selectinload

from llm_router.core.config import get_settings
from llm_router.core.security import Encryptor, generate_api_key, hash_api_key
from llm_router.domain.enums import ChangeType
from llm_router.domain.models import (
    ApiKey,
    BalanceLedger,
    DailyUsageSummary,
    LogicalModel,
    LogicalModelRoute,
    ProviderModel,
    RequestLog,
    UsageRecord,
)
from llm_router.services.cache.dual_cache import get_dual_cache


public_router = APIRouter(tags=["admin"])
protected_router = APIRouter(tags=["admin"])
settings = get_settings()
encryptor = Encryptor(settings.app_encryption_key)


def _format_decimal(value: Decimal | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        value = Decimal(value)
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return str(value.to_integral_value())
        return f"{value:f}".rstrip("0").rstrip(".")
    return str(value)


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _redirect_back(request: Request, fallback: str) -> RedirectResponse:
    referer = request.headers.get("referer")
    if referer:
        parsed = urlparse(referer)
        if parsed.path.startswith("/admin"):
            target = parsed.path
            if parsed.query:
                target = f"{target}?{parsed.query}"
            return _redirect(target)
    return _redirect(fallback)


def _to_decimal(value: str) -> Decimal | None:
    stripped = value.strip()
    if not stripped:
        return None
    return Decimal(stripped)


async def _invalidate_apikey_cache(api_key: ApiKey) -> None:
    """失效 ApiKey 的缓存"""
    dual_cache = get_dual_cache()
    if dual_cache:
        await dual_cache.invalidate_apikey(api_key.key_hash, api_key.id)


async def _invalidate_provider_cache(provider_id: int) -> None:
    """失效 Provider 的缓存"""
    dual_cache = get_dual_cache()
    if dual_cache:
        await dual_cache.invalidate_provider(provider_id)


async def _invalidate_route_cache(logical_model_id: int) -> None:
    """失效路由缓存"""
    dual_cache = get_dual_cache()
    if dual_cache:
        await dual_cache.invalidate_routes(logical_model_id)


async def require_admin(request: Request) -> None:
    if not request.session.get("admin_user"):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin/login"},
        )


def _render_admin(
    request: Request,
    template: str,
    context: dict,
    *,
    nav_active: str,
    title: str,
    status_code: int = 200,
):
    payload = {"request": request, "nav_active": nav_active, "title": title}
    payload.update(context)
    return request.app.state.templates.TemplateResponse(request, template, payload, status_code=status_code)


@public_router.get("/login")
async def login_page(request: Request):
    if request.session.get("admin_user"):
        return _redirect("/admin")
    return request.app.state.templates.TemplateResponse(request, "login.html", {"request": request, "error": None})


@public_router.post("/login")
async def login_action(request: Request, username: str = Form(...), password: str = Form(...)):
    if request.app.state.admin_user_store.authenticate(username, password):
        request.session["admin_user"] = username
        return _redirect("/admin")
    return request.app.state.templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "error": "用户名或密码错误"},
        status_code=400,
    )


@public_router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return _redirect("/admin/login")


@protected_router.get("")
async def dashboard(request: Request, _: None = Depends(require_admin)):
    session = request.state.db
    api_keys = (await session.execute(select(ApiKey).order_by(ApiKey.id.desc()))).scalars().all()
    logical_models = (await session.execute(select(LogicalModel).order_by(LogicalModel.name.asc()))).scalars().all()
    provider_models = (await session.execute(select(ProviderModel).order_by(ProviderModel.name.asc()))).scalars().all()
    recent_logs = (await session.execute(select(RequestLog).order_by(desc(RequestLog.id)).limit(8))).scalars().all()
    ledgers = (await session.execute(select(BalanceLedger).order_by(desc(BalanceLedger.id)).limit(8))).scalars().all()
    daily_summaries = (await session.execute(select(DailyUsageSummary).order_by(desc(DailyUsageSummary.summary_date)).limit(8))).scalars().all()
    total_balance = sum((Decimal(item.balance) for item in api_keys), Decimal("0"))
    total_daily_spend = sum((Decimal(item.daily_spend_amount) for item in api_keys), Decimal("0"))
    return _render_admin(
        request,
        "dashboard.html",
        {
            "api_keys": api_keys,
            "logical_models": logical_models,
            "provider_models": provider_models,
            "recent_logs": recent_logs,
            "ledgers": ledgers,
            "daily_summaries": daily_summaries,
            "total_balance": total_balance,
            "total_daily_spend": total_daily_spend,
        },
        nav_active="dashboard",
        title="Dashboard",
    )


@protected_router.get("/api-keys")
async def api_keys_page(request: Request, _: None = Depends(require_admin)):
    session = request.state.db
    api_keys_result = (await session.execute(select(ApiKey).order_by(ApiKey.id.desc()))).scalars().all()
    api_keys = [
        {
            "id": key.id,
            "name": key.name,
            "status": key.status,
            "balance": str(key.balance),
            "daily_spend_amount": str(key.daily_spend_amount),
            "qps_limit": key.qps_limit,
            "daily_budget_limit": str(key.daily_budget_limit) if key.daily_budget_limit is not None else None,
            "allowed_logical_models_json": key.allowed_logical_models_json,
            "request_content_logging_enabled": key.request_content_logging_enabled,
            "response_content_logging_enabled": key.response_content_logging_enabled,
        }
        for key in api_keys_result
    ]
    logical_models_result = (await session.execute(select(LogicalModel).order_by(LogicalModel.name.asc()))).scalars().all()
    logical_models = [{"id": m.id, "name": m.name} for m in logical_models_result]
    return _render_admin(
        request,
        "api_keys.html",
        {"api_keys": api_keys, "logical_models": logical_models, "raw_api_key": request.session.pop("new_api_key", None)},
        nav_active="api_keys",
        title="API Keys",
    )


@protected_router.get("/logical-models")
async def logical_models_page(request: Request, _: None = Depends(require_admin)):
    session = request.state.db
    logical_models_result = (await session.execute(select(LogicalModel).order_by(LogicalModel.name.asc()))).scalars().all()
    logical_models = [
        {
            "id": model.id,
            "name": model.name,
            "description": model.description,
            "is_active": model.is_active,
        }
        for model in logical_models_result
    ]
    routes_result = (await session.execute(select(LogicalModelRoute).order_by(LogicalModelRoute.priority.asc()))).scalars().all()
    routes = [
        {
            "id": route.id,
            "logical_model_id": route.logical_model_id,
            "provider_model_id": route.provider_model_id,
            "priority": route.priority,
            "weight": route.weight,
            "is_fallback": route.is_fallback,
            "status": route.status,
        }
        for route in routes_result
    ]
    provider_models_result = (await session.execute(select(ProviderModel).order_by(ProviderModel.name.asc()))).scalars().all()
    provider_models = [{"id": pm.id, "name": pm.name} for pm in provider_models_result]
    return _render_admin(
        request,
        "logical_models.html",
        {
            "logical_models": logical_models,
            "routes": routes,
            "provider_models": provider_models,
        },
        nav_active="logical_models",
        title="Logical Models",
    )


@protected_router.get("/providers")
async def providers_page(request: Request, _: None = Depends(require_admin)):
    session = request.state.db
    provider_models_result = (await session.execute(select(ProviderModel).order_by(ProviderModel.name.asc()))).scalars().all()
    provider_models = [
        {
            "id": pm.id,
            "name": pm.name,
            "protocol": pm.protocol,
            "endpoint": pm.endpoint,
            "upstream_model_name": pm.upstream_model_name,
            "input_token_price": _format_decimal(pm.input_token_price),
            "output_token_price": _format_decimal(pm.output_token_price),
            "cache_read_token_price": _format_decimal(pm.cache_read_token_price),
            "cache_write_token_price": _format_decimal(pm.cache_write_token_price),
            "timeout_seconds": pm.timeout_seconds,
            "supports_prompt_cache": pm.supports_prompt_cache,
            "is_active": pm.is_active,
        }
        for pm in provider_models_result
    ]
    return _render_admin(
        request,
        "providers.html",
        {
            "provider_models": provider_models,
            "encryptor_note": "下游 provider key 入库前已加密。",
        },
        nav_active="providers",
        title="Providers",
    )


@protected_router.post("/api-keys")
async def create_api_key(
    request: Request,
    name: str = Form(...),
    balance: Decimal = Form(default=Decimal("0")),
    daily_budget_limit: str = Form(default=""),
    qps_limit: int = Form(default=5),
    allowed_models: str = Form(default=""),
    _: None = Depends(require_admin),
):
    session = request.state.db
    raw_key = generate_api_key()
    allowed = [item.strip() for item in allowed_models.split(",") if item.strip()]
    session.add(
        ApiKey(
            name=name,
            key_hash=hash_api_key(raw_key),
            balance=balance,
            daily_budget_limit=_to_decimal(daily_budget_limit),
            qps_limit=qps_limit,
            allowed_logical_models_json=allowed,
            request_content_logging_enabled=False,
            response_content_logging_enabled=False,
        )
    )
    await session.commit()
    request.session["new_api_key"] = raw_key
    return _redirect_back(request, "/admin/api-keys")


@protected_router.post("/api-keys/{api_key_id}")
async def update_api_key(
    request: Request,
    api_key_id: int,
    name: str = Form(...),
    status_text: str = Form(..., alias="status"),
    daily_budget_limit: str = Form(default=""),
    qps_limit: int = Form(default=5),
    allowed_models: str = Form(default=""),
    request_content_logging_enabled: bool = Form(default=False),
    response_content_logging_enabled: bool = Form(default=False),
    _: None = Depends(require_admin),
):
    session = request.state.db
    api_key = await session.get(ApiKey, api_key_id)
    if api_key is None:
        return JSONResponse({"ok": False, "error": "API Key 不存在"}, status_code=404)
    api_key.name = name
    api_key.status = status_text
    api_key.daily_budget_limit = _to_decimal(daily_budget_limit)
    api_key.qps_limit = qps_limit
    api_key.allowed_logical_models_json = [item.strip() for item in allowed_models.split(",") if item.strip()]
    api_key.request_content_logging_enabled = request_content_logging_enabled
    api_key.response_content_logging_enabled = response_content_logging_enabled
    await session.commit()
    await _invalidate_apikey_cache(api_key)
    return JSONResponse({"ok": True, "id": api_key_id})


@protected_router.post("/api-keys/{api_key_id}/topup")
async def topup_api_key(
    request: Request,
    api_key_id: int,
    amount: Decimal = Form(...),
    remark: str = Form(default=""),
    _: None = Depends(require_admin),
):
    session = request.state.db
    api_key = await session.get(ApiKey, api_key_id)
    if api_key is None:
        return _redirect("/admin/api-keys")
    balance_before = Decimal(api_key.balance)
    api_key.balance = balance_before + amount
    session.add(
        BalanceLedger(
            api_key_id=api_key.id,
            change_type=ChangeType.TOPUP.value,
            amount=amount,
            balance_before=balance_before,
            balance_after=api_key.balance,
            reference_type="admin",
            reference_id=request.session.get("admin_user", "admin"),
            remark=remark or "Manual topup",
        )
    )
    await session.commit()
    await _invalidate_apikey_cache(api_key)
    return _redirect_back(request, "/admin/api-keys")


@protected_router.post("/api-keys/{api_key_id}/delete")
async def delete_api_key(request: Request, api_key_id: int, _: None = Depends(require_admin)):
    session = request.state.db
    api_key = await session.get(ApiKey, api_key_id)
    if api_key is not None:
        api_key.status = "disabled"
        await session.commit()
        await _invalidate_apikey_cache(api_key)
    return _redirect_back(request, "/admin/api-keys")


@protected_router.post("/api-keys/{api_key_id}/enable")
async def enable_api_key(request: Request, api_key_id: int, _: None = Depends(require_admin)):
    session = request.state.db
    api_key = await session.get(ApiKey, api_key_id)
    if api_key is not None:
        api_key.status = "active"
        await session.commit()
        await _invalidate_apikey_cache(api_key)
    return JSONResponse({"ok": True, "id": api_key_id})


@protected_router.post("/logical-models")
async def create_logical_model(
    request: Request,
    name: str = Form(...),
    description: str = Form(default=""),
    _: None = Depends(require_admin),
):
    session = request.state.db
    session.add(LogicalModel(name=name, description=description or None))
    await session.commit()
    return _redirect_back(request, "/admin/logical-models")


@protected_router.post("/logical-models/{logical_model_id}")
async def update_logical_model(
    request: Request,
    logical_model_id: int,
    name: str = Form(...),
    description: str = Form(default=""),
    is_active: bool = Form(default=False),
    _: None = Depends(require_admin),
):
    session = request.state.db
    logical_model = await session.get(LogicalModel, logical_model_id)
    if logical_model is None:
        return _redirect("/admin/logical-models")
    logical_model.name = name
    logical_model.description = description or None
    logical_model.is_active = is_active
    await session.commit()
    return _redirect_back(request, "/admin/logical-models")


@protected_router.post("/provider-models")
async def create_provider_model(
    request: Request,
    name: str = Form(...),
    protocol: str = Form(...),
    endpoint: str = Form(...),
    upstream_model_name: str = Form(...),
    api_key_secret: str = Form(...),
    input_token_price: Decimal = Form(default=Decimal("0")),
    output_token_price: Decimal = Form(default=Decimal("0")),
    supports_prompt_cache: bool = Form(default=False),
    cache_read_token_price: Decimal = Form(default=Decimal("0")),
    cache_write_token_price: Decimal = Form(default=Decimal("0")),
    timeout_seconds: int = Form(default=120),
    _: None = Depends(require_admin),
):
    session = request.state.db
    session.add(
        ProviderModel(
            name=name,
            protocol=protocol,
            endpoint=endpoint,
            upstream_model_name=upstream_model_name,
            encrypted_api_key=encryptor.encrypt(api_key_secret),
            input_token_price=input_token_price,
            output_token_price=output_token_price,
            supports_prompt_cache=supports_prompt_cache,
            cache_read_token_price=cache_read_token_price,
            cache_write_token_price=cache_write_token_price,
            timeout_seconds=timeout_seconds,
        )
    )
    await session.commit()
    return _redirect_back(request, "/admin/providers")


@protected_router.post("/provider-models/{provider_model_id}")
async def update_provider_model(
    request: Request,
    provider_model_id: int,
    name: str = Form(...),
    protocol: str = Form(...),
    endpoint: str = Form(...),
    upstream_model_name: str = Form(...),
    api_key_secret: str = Form(default=""),
    input_token_price: Decimal = Form(default=Decimal("0")),
    output_token_price: Decimal = Form(default=Decimal("0")),
    supports_prompt_cache: bool = Form(default=False),
    cache_read_token_price: Decimal = Form(default=Decimal("0")),
    cache_write_token_price: Decimal = Form(default=Decimal("0")),
    timeout_seconds: int = Form(default=120),
    is_active: bool = Form(default=False),
    _: None = Depends(require_admin),
):
    session = request.state.db
    provider_model = await session.get(ProviderModel, provider_model_id)
    if provider_model is None:
        return _redirect("/admin/providers")
    provider_model.name = name
    provider_model.protocol = protocol
    provider_model.endpoint = endpoint
    provider_model.upstream_model_name = upstream_model_name
    if api_key_secret.strip():
        provider_model.encrypted_api_key = encryptor.encrypt(api_key_secret)
    provider_model.input_token_price = input_token_price
    provider_model.output_token_price = output_token_price
    provider_model.supports_prompt_cache = supports_prompt_cache
    provider_model.cache_read_token_price = cache_read_token_price
    provider_model.cache_write_token_price = cache_write_token_price
    provider_model.timeout_seconds = timeout_seconds
    provider_model.is_active = is_active
    await session.commit()
    await _invalidate_provider_cache(provider_model_id)
    return _redirect_back(request, "/admin/providers")


@protected_router.post("/provider-models/{provider_model_id}/delete")
async def delete_provider_model(request: Request, provider_model_id: int, _: None = Depends(require_admin)):
    session = request.state.db
    provider_model = await session.get(ProviderModel, provider_model_id)
    if provider_model is not None:
        provider_model.is_active = False
        await session.commit()
        await _invalidate_provider_cache(provider_model_id)
    return _redirect_back(request, "/admin/providers")


@protected_router.post("/routes")
async def create_route(
    request: Request,
    logical_model_id: int = Form(...),
    provider_model_id: int = Form(...),
    priority: int = Form(default=100),
    weight: int = Form(default=1),
    is_fallback: bool = Form(default=False),
    _: None = Depends(require_admin),
):
    session = request.state.db
    session.add(
        LogicalModelRoute(
            logical_model_id=logical_model_id,
            provider_model_id=provider_model_id,
            priority=priority,
            weight=weight,
            is_fallback=is_fallback,
        )
    )
    await session.commit()
    await _invalidate_route_cache(logical_model_id)
    return _redirect_back(request, "/admin/logical-models")


@protected_router.post("/routes/{route_id}")
async def update_route(
    request: Request,
    route_id: int,
    logical_model_id: int = Form(...),
    provider_model_id: int = Form(...),
    priority: int = Form(default=100),
    weight: int = Form(default=1),
    is_fallback: bool = Form(default=False),
    status_text: str = Form(default="active", alias="status"),
    _: None = Depends(require_admin),
):
    session = request.state.db
    route = await session.get(LogicalModelRoute, route_id)
    if route is None:
        return _redirect("/admin/logical-models")
    old_logical_model_id = route.logical_model_id
    route.logical_model_id = logical_model_id
    route.provider_model_id = provider_model_id
    route.priority = priority
    route.weight = weight
    route.is_fallback = is_fallback
    route.status = status_text
    await session.commit()
    # 失效新旧路由缓存
    await _invalidate_route_cache(old_logical_model_id)
    if logical_model_id != old_logical_model_id:
        await _invalidate_route_cache(logical_model_id)
    return _redirect_back(request, "/admin/logical-models")


@protected_router.post("/routes/{route_id}/delete")
async def delete_route(request: Request, route_id: int, _: None = Depends(require_admin)):
    session = request.state.db
    route = await session.get(LogicalModelRoute, route_id)
    if route is not None:
        logical_model_id = route.logical_model_id
        await session.delete(route)
        await session.commit()
        await _invalidate_route_cache(logical_model_id)
    return _redirect_back(request, "/admin/logical-models")


@protected_router.get("/requests")
async def request_logs_page(
    request: Request,
    request_id: str | None = Query(default=None),
    protocol: str | None = Query(default=None),
    status_filter: str | None = Query(default=None),
    _: None = Depends(require_admin),
):
    session = request.state.db
    stmt = (
        select(RequestLog)
        .options(selectinload(RequestLog.usage_record))
        .order_by(desc(RequestLog.id))
        .limit(200)
    )
    if request_id:
        stmt = stmt.where(or_(RequestLog.request_id.contains(request_id), RequestLog.upstream_request_id.contains(request_id)))
    if protocol:
        stmt = stmt.where(RequestLog.protocol == protocol)
    if status_filter in {"success", "failed"}:
        stmt = stmt.where(RequestLog.success.is_(status_filter == "success"))
    logs_result = (await session.execute(stmt)).scalars().all()
    logs = [
        {
            "id": log.id,
            "request_id": log.request_id,
            "protocol": log.protocol,
            "status_code": log.status_code,
            "success": log.success,
            "latency_ms": log.latency_ms,
            "api_key_id": log.api_key_id,
            "provider_model_id": log.provider_model_id,
            "usage_record": {
                "prompt_tokens": log.usage_record.prompt_tokens,
                "completion_tokens": log.usage_record.completion_tokens,
                "cost_total": str(log.usage_record.cost_total),
            } if log.usage_record else None,
        }
        for log in logs_result
    ]
    return _render_admin(
        request,
        "request_logs.html",
        {
            "logs": logs,
            "filters": {
                "request_id": request_id or "",
                "protocol": protocol or "",
                "status_filter": status_filter or "",
            },
        },
        nav_active="requests",
        title="Request Logs",
    )


@protected_router.get("/requests/{request_log_id}")
async def request_log_detail(request: Request, request_log_id: int, _: None = Depends(require_admin)):
    session = request.state.db
    log = (
        await session.execute(
            select(RequestLog).options(selectinload(RequestLog.usage_record)).where(RequestLog.id == request_log_id)
        )
    ).scalar_one_or_none()
    if log is None:
        return _redirect("/admin/requests")
    return _render_admin(request, "request_detail.html", {"log": log}, nav_active="requests", title="Request Detail")


@protected_router.get("/billing")
async def billing_page(
    request: Request,
    api_key_id: int | None = Query(default=None),
    _: None = Depends(require_admin),
):
    session = request.state.db
    api_keys_result = (await session.execute(select(ApiKey).order_by(ApiKey.name.asc()))).scalars().all()
    api_keys = [{"id": key.id, "name": key.name} for key in api_keys_result]
    
    ledger_stmt = select(BalanceLedger).order_by(desc(BalanceLedger.id)).limit(100)
    usage_stmt = (
        select(UsageRecord)
        .options(selectinload(UsageRecord.request_log))
        .order_by(desc(UsageRecord.id))
        .limit(100)
    )
    summary_stmt = select(DailyUsageSummary).order_by(desc(DailyUsageSummary.summary_date)).limit(100)
    if api_key_id is not None:
        ledger_stmt = ledger_stmt.where(BalanceLedger.api_key_id == api_key_id)
        summary_stmt = summary_stmt.where(DailyUsageSummary.api_key_id == api_key_id)
        usage_stmt = usage_stmt.join(RequestLog, UsageRecord.request_log_id == RequestLog.id).where(RequestLog.api_key_id == api_key_id)

    ledgers_result = (await session.execute(ledger_stmt)).scalars().all()
    ledgers = [
        {
            "change_type": item.change_type,
            "amount": str(item.amount),
            "api_key_id": item.api_key_id,
            "balance_before": str(item.balance_before),
            "balance_after": str(item.balance_after),
            "remark": item.remark,
        }
        for item in ledgers_result
    ]

    usage_records_result = (await session.execute(usage_stmt)).scalars().all()
    usage_records = [
        {
            "request_log_id": item.request_log_id,
            "cost_total": str(item.cost_total),
            "prompt_tokens": item.prompt_tokens,
            "completion_tokens": item.completion_tokens,
            "request_log": {"request_id": item.request_log.request_id} if item.request_log else None,
        }
        for item in usage_records_result
    ]

    summaries_result = (await session.execute(summary_stmt)).scalars().all()
    summaries = [
        {
            "api_key_id": item.api_key_id,
            "summary_date": item.summary_date.isoformat(),
            "request_count": item.request_count,
            "cost_total": str(item.cost_total),
            "prompt_tokens": item.prompt_tokens,
            "completion_tokens": item.completion_tokens,
        }
        for item in summaries_result
    ]

    return _render_admin(
        request,
        "billing.html",
        {
            "api_keys": api_keys,
            "ledgers": ledgers,
            "usage_records": usage_records,
            "summaries": summaries,
            "selected_api_key_id": api_key_id,
        },
        nav_active="billing",
        title="Billing",
    )
