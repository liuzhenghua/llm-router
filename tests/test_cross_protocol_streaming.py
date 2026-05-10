import json

from llm_router.services.streaming_handlers.cross_protocol import (
    AnthropicOverOpenAIStreamingHandler,
    OpenAIOverAnthropicStreamingHandler,
)


def _decode_sse_events(events: list[bytes]) -> list[dict]:
    decoded = []
    for event in events:
        lines = event.decode("utf-8").strip().splitlines()
        data_line = next(line for line in lines if line.startswith("data: "))
        decoded.append(json.loads(data_line.removeprefix("data: ")))
    return decoded


def _decode_openai_chunks(events: list[bytes]) -> list[dict]:
    decoded = []
    for event in events:
        data = event.decode("utf-8").strip().removeprefix("data: ")
        if data != "[DONE]":
            decoded.append(json.loads(data))
    return decoded


def test_anthropic_over_openai_handles_non_contiguous_tool_call_index():
    handler = AnthropicOverOpenAIStreamingHandler()

    first_events = handler._process_openai_chunk({
        "id": "chatcmpl_123",
        "model": "gpt-test",
        "choices": [{
            "delta": {
                "role": "assistant",
                "tool_calls": [{
                    "index": 1,
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup_weather", "arguments": "{\"city\""},
                }],
            },
            "finish_reason": None,
        }],
    })
    second_events = handler._process_openai_chunk({
        "choices": [{
            "delta": {
                "tool_calls": [{
                    "index": 1,
                    "function": {"arguments": ":\"Paris\"}"},
                }],
            },
            "finish_reason": "tool_calls",
        }],
    })

    decoded_events = _decode_sse_events(first_events + second_events)
    tool_start = next(event for event in decoded_events if event["type"] == "content_block_start")
    assert tool_start["content_block"]["type"] == "tool_use"
    assert tool_start["content_block"]["id"] == "call_1"
    assert tool_start["content_block"]["name"] == "lookup_weather"

    response = json.loads(handler.get_accumulated_response())
    assert response["stop_reason"] == "tool_use"
    assert response["content"] == [{
        "type": "tool_use",
        "id": "call_1",
        "name": "lookup_weather",
        "input": {"city": "Paris"},
    }]


def test_anthropic_over_openai_streams_reasoning_as_thinking_delta():
    handler = AnthropicOverOpenAIStreamingHandler()

    events = handler._process_openai_chunk({
        "id": "chatcmpl_123",
        "model": "gpt-test",
        "choices": [{
            "delta": {
                "role": "assistant",
                "reasoning_content": "Let me inspect this first.",
            },
            "finish_reason": None,
        }],
    })
    events += handler._process_openai_chunk({
        "choices": [{
            "delta": {"content": "Done."},
            "finish_reason": "stop",
        }],
    })
    events += handler._build_finish_events()

    decoded_events = _decode_sse_events(events)
    thinking_start = next(
        event for event in decoded_events
        if event["type"] == "content_block_start"
        and event["content_block"]["type"] == "thinking"
    )
    thinking_delta = next(
        event for event in decoded_events
        if event["type"] == "content_block_delta"
        and event["delta"]["type"] == "thinking_delta"
    )
    text_delta = next(
        event for event in decoded_events
        if event["type"] == "content_block_delta"
        and event["delta"]["type"] == "text_delta"
    )

    assert thinking_start["index"] == 0
    assert thinking_delta["delta"]["thinking"] == "Let me inspect this first."
    assert text_delta["delta"]["text"] == "Done."

    response = json.loads(handler.get_accumulated_response())
    assert response["content"] == [
        {"type": "thinking", "thinking": "Let me inspect this first."},
        {"type": "text", "text": "Done."},
    ]


def test_openai_over_anthropic_streams_thinking_as_reasoning_content():
    handler = OpenAIOverAnthropicStreamingHandler()

    chunks = handler._process_anthropic_event("message_start", {
        "type": "message_start",
        "message": {
            "id": "msg_123",
            "model": "claude-test",
            "usage": {"input_tokens": 3, "output_tokens": 0},
        },
    })
    chunks += handler._process_anthropic_event("content_block_start", {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "thinking", "thinking": ""},
    })
    chunks += handler._process_anthropic_event("content_block_delta", {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "thinking_delta", "thinking": "Let me inspect this first."},
    })
    chunks += handler._process_anthropic_event("content_block_stop", {
        "type": "content_block_stop",
        "index": 0,
    })
    chunks += handler._process_anthropic_event("content_block_delta", {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "text_delta", "text": "Done."},
    })

    decoded_chunks = _decode_openai_chunks(chunks)
    reasoning_chunk = next(
        chunk for chunk in decoded_chunks
        if chunk["choices"][0]["delta"].get("reasoning_content")
    )
    text_chunk = next(
        chunk for chunk in decoded_chunks
        if chunk["choices"][0]["delta"].get("content") == "Done."
    )

    assert reasoning_chunk["choices"][0]["delta"]["reasoning_content"] == "Let me inspect this first."
    assert text_chunk["choices"][0]["delta"]["content"] == "Done."

    response = json.loads(handler.get_accumulated_response())
    message = response["choices"][0]["message"]
    assert message["reasoning_content"] == "Let me inspect this first."
    assert message["content"] == "Done."
