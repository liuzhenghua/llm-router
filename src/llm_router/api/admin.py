from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import desc, func, or_, select, update
from sqlalchemy.orm import selectinload

from llm_router.core.admin_users import AdminUserService
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

_admin_user_service = AdminUserService()


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


def _parse_logging_flag(value: str) -> bool | None:
    """Parse form logging flag: 'true' -> True, 'false' -> False, '' -> None (use global default)."""
    if value == "true":
        return True
    elif value == "false":
        return False
    return None


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
    session = request.state.db
    if not await _admin_user_service.has_any_user(session):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin/setup"},
        )
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


@public_router.get("/setup")
async def setup_page(request: Request):
    session = request.state.db
    if await _admin_user_service.has_any_user(session):
        return _redirect("/admin/login")
    return request.app.state.templates.TemplateResponse(
        request, "setup.html", {"request": request, "error": None}
    )


@public_router.post("/setup")
async def setup_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    session = request.state.db
    if await _admin_user_service.has_any_user(session):
        return _redirect("/admin/login")
    if not username.strip():
        return request.app.state.templates.TemplateResponse(
            request, "setup.html", {"request": request, "error": "用户名不能为空"}, status_code=400
        )
    if len(password) < 6:
        return request.app.state.templates.TemplateResponse(
            request, "setup.html", {"request": request, "error": "密码长度至少 6 位"}, status_code=400
        )
    if password != confirm_password:
        return request.app.state.templates.TemplateResponse(
            request, "setup.html", {"request": request, "error": "两次密码不一致"}, status_code=400
        )
    await _admin_user_service.create_or_update_user(session, username.strip(), password)
    request.session["admin_user"] = username.strip()
    return _redirect("/admin")


@public_router.get("/login")
async def login_page(request: Request):
    if request.session.get("admin_user"):
        return _redirect("/admin")
    session = request.state.db
    if not await _admin_user_service.has_any_user(session):
        return _redirect("/admin/setup")
    return request.app.state.templates.TemplateResponse(request, "login.html", {"request": request, "error": None})


@public_router.post("/login")
async def login_action(request: Request, username: str = Form(...), password: str = Form(...)):
    session = request.state.db
    if await _admin_user_service.authenticate(session, username, password):
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
    recent_logs = (await session.execute(
        select(RequestLog)
        .order_by(desc(RequestLog.id))
        .limit(8)
    )).scalars().all()
    ledgers = (await session.execute(select(BalanceLedger).order_by(desc(BalanceLedger.id)).limit(8))).scalars().all()
    daily_summaries = (await session.execute(select(DailyUsageSummary).order_by(desc(DailyUsageSummary.summary_date)).limit(8))).scalars().all()
    total_balance = sum((Decimal(item.balance) for item in api_keys), Decimal("0"))
    total_daily_spend = sum((Decimal(item.daily_spend_amount) for item in api_keys), Decimal("0"))
    api_key_name_map = {key.id: key.name for key in api_keys}
    return _render_admin(
        request,
        "dashboard.html",
        {
            "api_keys": api_keys,
            "logical_models": logical_models,
            "provider_models": provider_models,
            "recent_logs": recent_logs,
            "ledgers": [
                {
                    "change_type": item.change_type,
                    "amount": item.amount,
                    "api_key_id": item.api_key_id,
                    "api_key_name": api_key_name_map.get(item.api_key_id, f"#{item.api_key_id}"),
                }
                for item in ledgers
            ],
            "daily_summaries": [
                {
                    "summary_date": item.summary_date,
                    "api_key_id": item.api_key_id,
                    "api_key_name": api_key_name_map.get(item.api_key_id, f"#{item.api_key_id}"),
                    "cost_total": item.cost_total,
                }
                for item in daily_summaries
            ],
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
            "end_user": key.end_user or "",
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
            "openai_endpoint": pm.openai_endpoint or "",
            "anthropic_endpoint": pm.anthropic_endpoint or "",
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
    end_user: str = Form(default=""),
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
            request_content_logging_enabled=None,
            response_content_logging_enabled=None,
            end_user=end_user.strip() or None,
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
    request_content_logging_enabled: str = Form(default=""),
    response_content_logging_enabled: str = Form(default=""),
    end_user: str = Form(default=""),
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
    api_key.request_content_logging_enabled = _parse_logging_flag(request_content_logging_enabled)
    api_key.response_content_logging_enabled = _parse_logging_flag(response_content_logging_enabled)
    api_key.end_user = end_user.strip() or None
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


@protected_router.post("/api-keys/{api_key_id}/destroy")
async def destroy_api_key(request: Request, api_key_id: int, _: None = Depends(require_admin)):
    """永久删除 API Key 及其关联数据"""
    session = request.state.db
    api_key = await session.get(ApiKey, api_key_id)
    if api_key is None:
        return JSONResponse({"ok": False, "error": "API Key 不存在"}, status_code=404)
    # 先失效缓存
    await _invalidate_apikey_cache(api_key)
    # 将 RequestLog 中的 api_key_id 置为 NULL（FK nullable，无 CASCADE）
    await session.execute(update(RequestLog).where(RequestLog.api_key_id == api_key_id).values(api_key_id=None))
    # 删除 API Key（BalanceLedger / DailyUsageSummary 有 ondelete=CASCADE）
    await session.delete(api_key)
    await session.commit()
    return JSONResponse({"ok": True})


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
    openai_endpoint: str = Form(default=""),
    anthropic_endpoint: str = Form(default=""),
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
    oe = openai_endpoint.strip()
    ae = anthropic_endpoint.strip()
    if not oe and not ae:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="At least one of openai_endpoint or anthropic_endpoint is required")
    session = request.state.db
    session.add(
        ProviderModel(
            name=name,
            openai_endpoint=oe or None,
            anthropic_endpoint=ae or None,
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
    openai_endpoint: str = Form(default=""),
    anthropic_endpoint: str = Form(default=""),
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
    oe = openai_endpoint.strip()
    ae = anthropic_endpoint.strip()
    if not oe and not ae:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="At least one of openai_endpoint or anthropic_endpoint is required")
    session = request.state.db
    provider_model = await session.get(ProviderModel, provider_model_id)
    if provider_model is None:
        return _redirect("/admin/providers")
    provider_model.name = name
    provider_model.openai_endpoint = oe or None
    provider_model.anthropic_endpoint = ae or None
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


@protected_router.post("/routes/{route_id}/recover")
async def recover_route(request: Request, route_id: int, _: None = Depends(require_admin)) -> JSONResponse:
    """
    手动恢复降级路由为 active 状态

    清除路由的降级状态，使其重新参与路由调度
    """
    from llm_router.services.cache.degraded_cache import DegradedRouteCache

    dual_cache = get_dual_cache()
    if not dual_cache:
        return JSONResponse(
            status_code=500,
            content={"detail": "Cache not available"},
        )

    degraded_cache = DegradedRouteCache(dual_cache)
    recovered = await degraded_cache.recover(route_id)

    if recovered:
        return JSONResponse(
            status_code=200,
            content={"detail": "Route recovered successfully"},
        )
    else:
        return JSONResponse(
            status_code=404,
            content={"detail": "Route not in degraded state"},
        )


@protected_router.get("/routes/{route_id}/degraded-status")
async def get_route_degraded_status(
    request: Request,
    route_id: int,
    _: None = Depends(require_admin),
) -> JSONResponse:
    """
    获取路由的降级状态
    """
    from llm_router.services.cache.degraded_cache import DegradedRouteCache

    dual_cache = get_dual_cache()
    if not dual_cache:
        return JSONResponse(
            status_code=500,
            content={"detail": "Cache not available"},
        )

    degraded_cache = DegradedRouteCache(dual_cache)
    status = await degraded_cache.get_status(route_id)

    if status is None:
        return JSONResponse(
            status_code=200,
            content={"degraded": False},
        )
    else:
        return JSONResponse(
            status_code=200,
            content={
                "degraded": True,
                "degraded_type": status.degraded_type.value,
                "fail_count": status.fail_count,
                "last_fail_time": status.last_fail_time,
            },
        )


@protected_router.get("/requests")
async def request_logs_page(
    request: Request,
    request_id: str | None = Query(default=None),
    upstream_request_id: str | None = Query(default=None),
    started_after: datetime | None = Query(default=None),
    started_before: datetime | None = Query(default=None),
    api_key_id: int | None = Query(default=None),
    provider_model_id: int | None = Query(default=None),
    end_user: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    _: None = Depends(require_admin),
):
    session = request.state.db
    per_page = settings.admin_page_size
    stmt = (
        select(RequestLog)
        .options(
            selectinload(RequestLog.usage_record),
            selectinload(RequestLog.api_key),
            selectinload(RequestLog.provider_model),
        )
        .order_by(desc(RequestLog.id))
    )
    count_stmt = select(func.count()).select_from(RequestLog)
    if request_id:
        stmt = stmt.where(RequestLog.request_id.contains(request_id))
        count_stmt = count_stmt.where(RequestLog.request_id.contains(request_id))
    if upstream_request_id:
        stmt = stmt.where(RequestLog.upstream_request_id.contains(upstream_request_id))
        count_stmt = count_stmt.where(RequestLog.upstream_request_id.contains(upstream_request_id))
    if started_after:
        stmt = stmt.where(RequestLog.started_at >= started_after)
        count_stmt = count_stmt.where(RequestLog.started_at >= started_after)
    if started_before:
        stmt = stmt.where(RequestLog.started_at <= started_before)
        count_stmt = count_stmt.where(RequestLog.started_at <= started_before)
    if api_key_id:
        stmt = stmt.where(RequestLog.api_key_id == api_key_id)
        count_stmt = count_stmt.where(RequestLog.api_key_id == api_key_id)
    if provider_model_id:
        stmt = stmt.where(RequestLog.provider_model_id == provider_model_id)
        count_stmt = count_stmt.where(RequestLog.provider_model_id == provider_model_id)
    if end_user:
        stmt = stmt.where(RequestLog.end_user.contains(end_user))
        count_stmt = count_stmt.where(RequestLog.end_user.contains(end_user))
    total = (await session.execute(count_stmt)).scalar() or 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    stmt = stmt.limit(per_page).offset((page - 1) * per_page)
    logs_result = (await session.execute(stmt)).scalars().all()
    pagination = {
        "page": page,
        "pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_num": page - 1 if page > 1 else None,
        "next_num": page + 1 if page < total_pages else None,
    }
    logs = [
        {
            "id": log.id,
            "request_id": log.request_id,
            "upstream_request_id": log.upstream_request_id,
            "started_at": log.started_at.isoformat() if log.started_at else None,
            "protocol": log.protocol,
            "status_code": log.status_code,
            "success": log.success,
            "latency_ms": log.latency_ms,
            "call_type": log.call_type,
            "api_key_id": log.api_key_id,
            "provider_model_id": log.provider_model_id,
            "api_key_name": log.api_key.name if log.api_key else None,
            "provider_model_name": log.provider_model.name if log.provider_model else None,
            "end_user": log.end_user or "",
            "usage_record": {
                "prompt_tokens": log.usage_record.prompt_tokens,
                "completion_tokens": log.usage_record.completion_tokens,
                "reasoning_tokens": log.usage_record.reasoning_tokens,
                "cost_total": log.usage_record.cost_total,
            } if log.usage_record else None,
        }
        for log in logs_result
    ]
    api_keys_list = (await session.execute(select(ApiKey).order_by(ApiKey.name.asc()))).scalars().all()
    provider_models_list = (await session.execute(select(ProviderModel).order_by(ProviderModel.name.asc()))).scalars().all()
    return _render_admin(
        request,
        "request_logs.html",
        {
            "logs": logs,
            "filters": {
                "request_id": request_id or "",
                "upstream_request_id": upstream_request_id or "",
                "started_after": started_after.strftime("%Y-%m-%dT%H:%M:%S") + "Z" if started_after else "",
                "started_before": started_before.strftime("%Y-%m-%dT%H:%M:%S") + "Z" if started_before else "",
                "api_key_id": api_key_id or "",
                "provider_model_id": provider_model_id or "",
                "end_user": end_user or "",
            },
            "api_keys_list": [{"id": k.id, "name": k.name} for k in api_keys_list],
            "provider_models_list": [{"id": m.id, "name": m.name} for m in provider_models_list],
            "pagination": pagination,
        },
        nav_active="requests",
        title="Request Logs",
    )


@protected_router.get("/requests/{request_log_id}")
async def request_log_detail(request: Request, request_log_id: int, _: None = Depends(require_admin)):
    session = request.state.db
    log = (
        await session.execute(
            select(RequestLog).options(
                selectinload(RequestLog.usage_record),
                selectinload(RequestLog.api_key),
                selectinload(RequestLog.provider_model),
                selectinload(RequestLog.body),
            ).where(RequestLog.id == request_log_id)
        )
    ).scalar_one_or_none()
    if log is None:
        return _redirect("/admin/requests")

    # Get logical_model_name via LogicalModelRoute
    logical_model_name = None
    if log.provider_model_id:
        route = (
            await session.execute(
                select(LogicalModelRoute).where(LogicalModelRoute.provider_model_id == log.provider_model_id).limit(1)
            )
        ).scalar_one_or_none()
        if route:
            logical_model = (await session.execute(select(LogicalModel).where(LogicalModel.id == route.logical_model_id))).scalar_one_or_none()
            if logical_model:
                logical_model_name = logical_model.name

    log_dict = {
        "id": log.id,
        "request_id": log.request_id,
        "protocol": log.protocol,
        "status_code": log.status_code,
        "success": log.success,
        "latency_ms": log.latency_ms,
        "upstream_request_id": log.upstream_request_id,
        "created_at": log.created_at,
        "request_body": log.body.request_body if log.body else None,
        "response_body": log.body.response_body if log.body else None,
        "error_message": log.error_message,
        "call_type": log.call_type,
        "api_key_id": log.api_key_id,
        "logical_model_id": log.logical_model_id,
        "provider_model_id": log.provider_model_id,
        "api_key_name": log.api_key.name if log.api_key else None,
        "provider_model_name": log.provider_model.name if log.provider_model else None,
        "provider_model_protocol": log.protocol,
        "logical_model_name": logical_model_name,
        "end_user": log.end_user,
        "usage_record": {
            "prompt_tokens": log.usage_record.prompt_tokens,
            "completion_tokens": log.usage_record.completion_tokens,
            "cache_read_tokens": log.usage_record.cache_read_tokens,
            "cache_write_tokens": log.usage_record.cache_write_tokens,
            "reasoning_tokens": log.usage_record.reasoning_tokens,
            "cost_input": str(log.usage_record.cost_input),
            "cost_output": str(log.usage_record.cost_output),
            "cost_cache_read": str(log.usage_record.cost_cache_read),
            "cost_cache_write": str(log.usage_record.cost_cache_write),
            "cost_total": str(log.usage_record.cost_total),
        } if log.usage_record else None,
    }
    return _render_admin(request, "request_detail.html", {"log": log_dict}, nav_active="requests", title="Request Detail")


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
        .order_by(desc(UsageRecord.request_log_id))
        .limit(100)
    )
    summary_stmt = select(DailyUsageSummary).order_by(desc(DailyUsageSummary.summary_date)).limit(100)
    if api_key_id is not None:
        ledger_stmt = ledger_stmt.where(BalanceLedger.api_key_id == api_key_id)
        summary_stmt = summary_stmt.where(DailyUsageSummary.api_key_id == api_key_id)
        usage_stmt = usage_stmt.join(RequestLog, UsageRecord.request_log_id == RequestLog.id).where(RequestLog.api_key_id == api_key_id)

    api_key_map = {key.id: key.name for key in api_keys_result}

    ledgers_result = (await session.execute(ledger_stmt)).scalars().all()
    ledgers = [
        {
            "change_type": item.change_type,
            "amount": str(item.amount),
            "api_key_id": item.api_key_id,
            "api_key_name": api_key_map.get(item.api_key_id, f"#{item.api_key_id}"),
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
            "reasoning_tokens": item.reasoning_tokens,
            "request_log": {"request_id": item.request_log.request_id} if item.request_log else None,
        }
        for item in usage_records_result
    ]

    summaries_result = (await session.execute(summary_stmt)).scalars().all()
    summaries = [
        {
            "api_key_id": item.api_key_id,
            "api_key_name": api_key_map.get(item.api_key_id, f"#{item.api_key_id}"),
            "summary_date": item.summary_date.isoformat(),
            "request_count": item.request_count,
            "cost_total": str(item.cost_total),
            "prompt_tokens": item.prompt_tokens,
            "completion_tokens": item.completion_tokens,
            "reasoning_tokens": item.reasoning_tokens,
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


@protected_router.get("/docs")
async def docs_page(request: Request, _: None = Depends(require_admin)):
    base_url = str(request.base_url).rstrip("/")
    return _render_admin(
        request,
        "docs.html",
        {"base_url": base_url},
        nav_active="docs",
        title="Docs",
    )
