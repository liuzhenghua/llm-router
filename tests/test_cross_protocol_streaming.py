import json

from llm_router.services.streaming_handlers.cross_protocol import AnthropicOverOpenAIStreamingHandler


def _decode_sse_events(events: list[bytes]) -> list[dict]:
    decoded = []
    for event in events:
        lines = event.decode("utf-8").strip().splitlines()
        data_line = next(line for line in lines if line.startswith("data: "))
        decoded.append(json.loads(data_line.removeprefix("data: ")))
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
