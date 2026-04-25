from __future__ import annotations

import logging
import time

from fastapi import Request
from starlette.background import BackgroundTask

from llm_router.core.database import SessionLocal

logger = logging.getLogger(__name__)


async def db_session_middleware(request: Request, call_next):
    """Attach a DB session to each request and close it in a background task."""
    session = SessionLocal()
    request.state.db = session
    try:
        response = await call_next(request)
    except Exception:
        try:
            await session.close()
        except Exception:
            pass
        raise
    background = response.background

    async def close_session() -> None:
        try:
            await session.close()
        except Exception:
            logger.debug("DB session close failed (connection already lost), ignoring")
        if background is not None:
            await background()

    response.background = BackgroundTask(close_session)
    return response


async def request_log_middleware(request: Request, call_next):
    """Log request start and end (with status + latency). Skips /healthz."""
    if request.url.path == "/healthz":
        return await call_next(request)

    start = time.perf_counter()
    logger.info("→ %s %s", request.method, request.url.path)
    response = await call_next(request)

    # Capture values before the background task closes over them.
    # For streaming responses call_next() returns on the first header;
    # the background task fires after the body is fully flushed, giving
    # accurate end-to-end latency instead of time-to-first-header.
    status_code = response.status_code
    method = request.method
    path = request.url.path
    existing_bg = response.background

    async def log_end() -> None:
        cost_ms = int((time.perf_counter() - start) * 1000)
        access_context = getattr(request.state, "access_log_context", "-|-|-")
        logger.info(
            "← %s %s %s %dms",
            method,
            path,
            status_code,
            cost_ms,
            extra={"access_context": access_context},
        )
        if existing_bg is not None:
            await existing_bg()

    response.background = BackgroundTask(log_end)
    return response
