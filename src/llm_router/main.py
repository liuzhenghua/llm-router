from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import jinja2
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from llm_router.api import admin, anthropic, openai
from llm_router.api.health import router as health_router
from llm_router.core.config import get_settings
from llm_router.core.logging_config import setup_logging
from llm_router.exception_handlers import register_exception_handlers
from llm_router.lifespan import lifespan
from llm_router.middleware import db_session_middleware, request_log_middleware

BASE_PATH = Path(__file__).resolve().parent
settings = get_settings()
setup_logging(settings.log_dir, settings.log_level, settings.log_format)


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


def _format_datetime(value: str | None) -> str:
    """Return UTC ISO string with Z suffix; frontend JS converts to local timezone."""
    if value is None:
        return ""
    try:
        dt = datetime.fromisoformat(value)
        return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    except (ValueError, TypeError):
        return value


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

    # Middleware (registered last-in, first-run)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
    app.middleware("http")(db_session_middleware)
    app.middleware("http")(request_log_middleware)

    # Exception handlers
    register_exception_handlers(app)

    # Static files & Jinja2 templates
    app.mount("/static", StaticFiles(directory=BASE_PATH / "static"), name="static")
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(BASE_PATH / "templates")),
    )
    env.filters["format_decimal"] = _format_decimal
    env.filters["format_datetime"] = _format_datetime
    app.state.templates = Jinja2Templates(env=env)

    # Routers
    app.include_router(health_router)
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
        http="h11",
    )


if __name__ == "__main__":
    main()
