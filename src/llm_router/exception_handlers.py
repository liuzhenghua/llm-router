from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse, RedirectResponse

logger = logging.getLogger(__name__)


def _error_response(request: Request, status_code: int, message: str) -> JSONResponse:
    """Return a protocol-appropriate JSON error response based on the request path."""
    path = request.url.path
    if path.startswith("/v1/"):
        error_type = "server_error" if status_code >= 500 else "invalid_request_error"
        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "message": message,
                    "type": error_type,
                    "code": error_type,
                }
            },
        )
    if path.startswith("/anthropic/"):
        error_type = "api_error" if status_code >= 500 else "invalid_request_error"
        return JSONResponse(
            status_code=status_code,
            content={
                "type": "error",
                "error": {
                    "type": error_type,
                    "message": message,
                },
            },
        )
    # Admin / other routes
    return JSONResponse(status_code=status_code, content={"detail": message})


def register_exception_handlers(app: FastAPI) -> None:
    """Register all global exception handlers onto the given FastAPI app."""

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> RedirectResponse | JSONResponse:
        # Let redirect exceptions pass through as actual redirects
        if 300 <= exc.status_code < 400:
            location = (exc.headers or {}).get("Location") or "/"
            return RedirectResponse(url=location, status_code=exc.status_code)
        return _error_response(request, exc.status_code, exc.detail or "HTTP error")

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        message = "; ".join(
            f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        return _error_response(request, 422, message)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return _error_response(request, 500, "Internal server error")
