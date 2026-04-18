from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import jinja2
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.middleware.sessions import SessionMiddleware

from llm_router.api import admin, anthropic, openai
from llm_router.core.admin_users import AdminUserStore
from llm_router.core.config import get_settings
from llm_router.core.database import SessionLocal, init_db


BASE_PATH = Path(__file__).resolve().parent
settings = get_settings()


def _format_decimal(value: Decimal | float | str | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        value = Decimal(value)
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return str(value.to_integral_value())
        return f"{value:f}".rstrip("0").rstrip(".")
    return str(value)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
    app.mount("/static", StaticFiles(directory=BASE_PATH / "static"), name="static")
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(BASE_PATH / "templates")),
    )
    env.filters["format_decimal"] = _format_decimal
    app.state.templates = Jinja2Templates(env=env)
    app.state.admin_user_store = AdminUserStore(settings.admin_users_file)

    @app.middleware("http")
    async def db_session_middleware(request: Request, call_next):
        session = SessionLocal()
        request.state.db = session
        response = await call_next(request)
        background = response.background

        async def close_session() -> None:
            await session.close()
            if background is not None:
                await background()

        response.background = BackgroundTask(close_session)
        return response

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "mode": settings.app_mode}

    @app.get("/")
    async def root():
        return RedirectResponse("/admin", status_code=303)

    app.include_router(openai.router)
    app.include_router(anthropic.router)
    app.include_router(admin.public_router, prefix="/admin")
    app.include_router(admin.protected_router, prefix="/admin")
    return app


app = create_app()


def main() -> None:
    uvicorn.run(
        "llm_router.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        factory=False,
    )


if __name__ == "__main__":
    main()
