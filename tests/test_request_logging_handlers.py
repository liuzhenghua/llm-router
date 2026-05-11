import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from llm_router.domain.enums import ProviderProtocol
from llm_router.services.non_stream_handlers.cross_protocol import (
    AnthropicOverOpenAINonStreamHandler,
    OpenAIOverAnthropicNonStreamHandler,
)
from llm_router.services.non_stream_handlers.openai import (
    OpenAIEmbeddingNonStreamHandler,
    OpenAINonStreamHandler,
)
from llm_router.services.streaming_handlers.cross_protocol import (
    AnthropicOverOpenAIStreamingHandler,
    OpenAIOverAnthropicStreamingHandler,
)
from llm_router.services.streaming_handlers.openai import OpenAIStreamingHandler
from llm_router.services.streaming_handlers.anthropic import AnthropicStreamingHandler


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

    async def post(self, *args, **kwargs):
        return self._response


class _FakeStreamResponse:
    def __init__(self, status_code: int, body: str = "upstream failed"):
        self.status_code = status_code
        self._body = body
        self.headers = {}

    async def aread(self):
        return self._body.encode("utf-8")


class _FakeStreamContext:
    def __init__(self, response: _FakeStreamResponse):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeStreamingClient:
    def __init__(self, response: _FakeStreamResponse):
        self._response = response

    def stream(self, *args, **kwargs):
        return _FakeStreamContext(self._response)


class _FakeStreamOpenFailureContext:
    async def __aenter__(self):
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _FakeStreamOpenFailureClient:
    def stream(self, *args, **kwargs):
        return _FakeStreamOpenFailureContext()


def _make_provider(upstream_protocol: ProviderProtocol = ProviderProtocol.OPENAI) -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        endpoint="https://example.com/v1",
        api_key="secret",
        upstream_protocol=upstream_protocol,
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


async def _assert_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch,
    module_path: str,
    handler,
    payload: dict,
    request_path: str,
    upstream_protocol: ProviderProtocol = ProviderProtocol.OPENAI,
):
    captured = []
    response = _FakeResponse(429, "rate limited")

    monkeypatch.setattr(f"{module_path}.get_http_client", lambda: _FakeAsyncClient(response))
    monkeypatch.setattr(f"{module_path}.schedule_post_request_tasks", captured.append)

    with pytest.raises(HTTPException) as exc_info:
        await handler.proxy(
            None,
            api_key=None,
            context=_make_context(payload),
            provider=_make_provider(upstream_protocol),
            request_path=request_path,
        )

    assert exc_info.value.status_code == 429
    assert len(captured) == 1
    assert captured[0].status_code == 429
    assert captured[0].success is False
    assert captured[0].error_message == "rate limited"
    assert captured[0].provider_model_protocol == upstream_protocol.value


async def _assert_streaming_cross_protocol_failure_logs_original_payload(
    monkeypatch: pytest.MonkeyPatch,
    handler,
    payload: dict,
    request_path: str,
    upstream_protocol: ProviderProtocol = ProviderProtocol.OPENAI,
):
    captured = []
    response = _FakeStreamResponse(429, "rate limited")

    monkeypatch.setattr(
        "llm_router.services.streaming_handlers.cross_protocol.get_http_client",
        lambda: _FakeStreamingClient(response),
    )
    monkeypatch.setattr(
        "llm_router.services.streaming_handlers.cross_protocol.schedule_post_request_tasks",
        captured.append,
    )

    with pytest.raises(HTTPException) as exc_info:
        await handler.proxy(
            None,
            api_key=None,
            context=_make_context(payload),
            provider=_make_provider(upstream_protocol),
            request_path=request_path,
        )

    assert exc_info.value.status_code == 429
    assert len(captured) == 1
    assert captured[0].status_code == 429
    assert captured[0].success is False
    assert captured[0].provider_model_protocol == upstream_protocol.value
    assert json.loads(captured[0].request_body) == payload


async def _assert_stream_open_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch,
    module_path: str,
    handler,
    payload: dict,
    request_path: str,
    protocol: ProviderProtocol,
    upstream_protocol: ProviderProtocol,
    logs_original_payload: bool = False,
):
    captured = []

    monkeypatch.setattr(f"{module_path}.get_http_client", lambda: _FakeStreamOpenFailureClient())
    monkeypatch.setattr(f"{module_path}.schedule_post_request_tasks", captured.append)

    with pytest.raises(HTTPException) as exc_info:
        await handler.proxy(
            None,
            api_key=None,
            context=_make_context(payload),
            provider=_make_provider(upstream_protocol),
            request_path=request_path,
        )

    assert exc_info.value.status_code == 502
    assert len(captured) == 1
    assert captured[0].status_code == 502
    assert captured[0].success is False
    assert "Server disconnected without sending a response" in captured[0].error_message
    assert captured[0].protocol == protocol.value
    assert captured[0].provider_model_protocol == upstream_protocol.value
    if logs_original_payload:
        assert json.loads(captured[0].request_body) == payload


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
        ProviderProtocol.ANTHROPIC,
    )


@pytest.mark.asyncio
async def test_anthropic_over_openai_streaming_logs_original_payload(monkeypatch: pytest.MonkeyPatch):
    await _assert_streaming_cross_protocol_failure_logs_original_payload(
        monkeypatch,
        AnthropicOverOpenAIStreamingHandler(),
        {"model": "client-model", "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]},
        "/messages",
    )


@pytest.mark.asyncio
async def test_openai_over_anthropic_streaming_logs_original_payload(monkeypatch: pytest.MonkeyPatch):
    await _assert_streaming_cross_protocol_failure_logs_original_payload(
        monkeypatch,
        OpenAIOverAnthropicStreamingHandler(),
        {"model": "client-model", "messages": [{"role": "user", "content": "hi"}]},
        "/chat/completions",
        ProviderProtocol.ANTHROPIC,
    )


@pytest.mark.asyncio
async def test_openai_streaming_logs_stream_open_failure(monkeypatch: pytest.MonkeyPatch):
    await _assert_stream_open_failure_is_logged(
        monkeypatch,
        "llm_router.services.streaming_handlers.openai",
        OpenAIStreamingHandler(),
        {"model": "client-model", "messages": [{"role": "user", "content": "hi"}]},
        "/chat/completions",
        ProviderProtocol.OPENAI,
        ProviderProtocol.OPENAI,
    )


@pytest.mark.asyncio
async def test_anthropic_streaming_logs_stream_open_failure(monkeypatch: pytest.MonkeyPatch):
    await _assert_stream_open_failure_is_logged(
        monkeypatch,
        "llm_router.services.streaming_handlers.anthropic",
        AnthropicStreamingHandler(),
        {"model": "client-model", "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]},
        "/messages",
        ProviderProtocol.ANTHROPIC,
        ProviderProtocol.ANTHROPIC,
    )


@pytest.mark.asyncio
async def test_anthropic_over_openai_streaming_logs_stream_open_failure(monkeypatch: pytest.MonkeyPatch):
    payload = {"model": "client-model", "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]}
    await _assert_stream_open_failure_is_logged(
        monkeypatch,
        "llm_router.services.streaming_handlers.cross_protocol",
        AnthropicOverOpenAIStreamingHandler(),
        payload,
        "/messages",
        ProviderProtocol.ANTHROPIC,
        ProviderProtocol.OPENAI,
        logs_original_payload=True,
    )


@pytest.mark.asyncio
async def test_openai_over_anthropic_streaming_logs_stream_open_failure(monkeypatch: pytest.MonkeyPatch):
    payload = {"model": "client-model", "messages": [{"role": "user", "content": "hi"}]}
    await _assert_stream_open_failure_is_logged(
        monkeypatch,
        "llm_router.services.streaming_handlers.cross_protocol",
        OpenAIOverAnthropicStreamingHandler(),
        payload,
        "/chat/completions",
        ProviderProtocol.OPENAI,
        ProviderProtocol.ANTHROPIC,
        logs_original_payload=True,
    )
