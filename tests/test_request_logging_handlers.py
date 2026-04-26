from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from llm_router.services.non_stream_handlers.cross_protocol import (
    AnthropicOverOpenAINonStreamHandler,
    OpenAIOverAnthropicNonStreamHandler,
)
from llm_router.services.non_stream_handlers.openai import (
    OpenAIEmbeddingNonStreamHandler,
    OpenAINonStreamHandler,
)


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "upstream failed"):
        self.status_code = status_code
        self.text = text
        self.headers = {}

    def json(self):
        return {}


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        return self._response


def _make_provider() -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        endpoint="https://example.com/v1",
        api_key="secret",
        upstream_model_name="gpt-4o",
        timeout_seconds=30,
        input_token_price=Decimal("1"),
        output_token_price=Decimal("1"),
        cache_read_token_price=Decimal("0"),
        cache_write_token_price=Decimal("0"),
    )


def _make_context(payload: dict, *, logical_model_name: str = "test-model", headers: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        request_id="req_123",
        payload=payload,
        api_key_id=11,
        logical_model_id=22,
        logical_model_name=logical_model_name,
        request_logging_enabled=True,
        response_logging_enabled=True,
        end_user="end-user",
        channel="web",
        api_key_timezone="UTC",
        headers=headers or {},
    )


async def _assert_failure_is_logged(monkeypatch: pytest.MonkeyPatch, module_path: str, handler, payload: dict, request_path: str):
    captured = []
    response = _FakeResponse(429, "rate limited")

    monkeypatch.setattr(f"{module_path}.httpx.AsyncClient", lambda timeout: _FakeAsyncClient(response))
    monkeypatch.setattr(f"{module_path}.schedule_post_request_tasks", captured.append)

    with pytest.raises(HTTPException) as exc_info:
        await handler.proxy(
            None,
            api_key=None,
            context=_make_context(payload),
            provider=_make_provider(),
            request_path=request_path,
        )

    assert exc_info.value.status_code == 429
    assert len(captured) == 1
    assert captured[0].status_code == 429
    assert captured[0].success is False
    assert captured[0].error_message == "rate limited"


@pytest.mark.asyncio
async def test_openai_non_stream_logs_upstream_http_failure(monkeypatch: pytest.MonkeyPatch):
    await _assert_failure_is_logged(
        monkeypatch,
        "llm_router.services.non_stream_handlers.openai",
        OpenAINonStreamHandler(),
        {"model": "ignored", "messages": [{"role": "user", "content": "hi"}]},
        "/chat/completions",
    )


@pytest.mark.asyncio
async def test_openai_embedding_logs_upstream_http_failure(monkeypatch: pytest.MonkeyPatch):
    await _assert_failure_is_logged(
        monkeypatch,
        "llm_router.services.non_stream_handlers.openai",
        OpenAIEmbeddingNonStreamHandler(),
        {"model": "ignored", "input": "hello"},
        "/embeddings",
    )


@pytest.mark.asyncio
async def test_anthropic_over_openai_logs_upstream_http_failure(monkeypatch: pytest.MonkeyPatch):
    await _assert_failure_is_logged(
        monkeypatch,
        "llm_router.services.non_stream_handlers.cross_protocol",
        AnthropicOverOpenAINonStreamHandler(),
        {"model": "ignored", "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]},
        "/messages",
    )


@pytest.mark.asyncio
async def test_openai_over_anthropic_logs_upstream_http_failure(monkeypatch: pytest.MonkeyPatch):
    await _assert_failure_is_logged(
        monkeypatch,
        "llm_router.services.non_stream_handlers.cross_protocol",
        OpenAIOverAnthropicNonStreamHandler(),
        {"model": "ignored", "messages": [{"role": "user", "content": "hi"}]},
        "/chat/completions",
    )
