"""Shared httpx.AsyncClient for all upstream LLM provider requests.

A single client with a connection pool is reused across all requests.
Timeouts are passed per-request (not set on the client) so different
providers can carry different read timeouts without creating new clients.
"""
from __future__ import annotations

import httpx

_http_client: httpx.AsyncClient | None = None


def init_http_client() -> httpx.AsyncClient:
    """Create and register the global HTTP client. Call once at startup."""
    global _http_client
    _http_client = httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=200,
            max_keepalive_connections=20,
            keepalive_expiry=60,
        ),
        # No default timeout — callers pass per-request timeout via the
        # `timeout=` argument on each .post() / .get() / .stream() call.
        timeout=None,
    )
    return _http_client


def get_http_client() -> httpx.AsyncClient:
    """Return the global HTTP client.  Must be called after init_http_client()."""
    if _http_client is None:
        raise RuntimeError(
            "HTTP client has not been initialised. "
            "Call init_http_client() during application startup."
        )
    return _http_client


async def close_http_client() -> None:
    """Close the global HTTP client. Call once at shutdown."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
