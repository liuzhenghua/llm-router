from __future__ import annotations

from fastapi import APIRouter
from starlette.responses import RedirectResponse

from llm_router.core.config import get_settings

router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/healthz")
async def healthz():
    return {"status": "ok", "redis_enabled": settings.redis_enabled, "use_mysql": settings.use_mysql}


@router.get("/")
async def root():
    return RedirectResponse("/admin", status_code=303)
