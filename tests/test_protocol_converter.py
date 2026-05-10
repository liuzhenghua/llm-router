import pytest
from fastapi import HTTPException, status

from llm_router.services.protocol_converter import (
    anthropic_to_openai_request,
    anthropic_to_openai_response,
    openai_to_anthropic_request,
    openai_to_anthropic_response,
)


def test_anthropic_to_openai_preserves_openai_style_image_url_blocks():
    payload = {
        "model": "glm-5.1",
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "OCR the following image into Markdown.",
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,abc123",
                        "detail": "high",
                    },
                },
            ],
        }],
        "max_tokens": 4096,
        "stream": False,
    }

    result = anthropic_to_openai_request(payload, "z-ai/glm-5.1")

    assert result["model"] == "z-ai/glm-5.1"
    assert result["messages"] == [{
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "OCR the following image into Markdown.",
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64,abc123",
                    "detail": "high",
                },
            },
        ],
    }]
    assert result["max_tokens"] == 4096
    assert result["stream"] is False


@pytest.mark.parametrize("tool", [{}, {"name": ""}, {"name": "   "}])
def test_anthropic_to_openai_requires_tool_name(tool):
    payload = {
        "model": "claude",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [tool],
    }

    with pytest.raises(HTTPException) as exc_info:
        anthropic_to_openai_request(payload, "gpt-4o")

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.detail == "tools[0].name is required"


def test_openai_to_anthropic_response_preserves_reasoning_content():
    body = {
        "id": "chatcmpl_123",
        "choices": [{
            "message": {
                "role": "assistant",
                "reasoning_content": "I should inspect the route first.",
                "content": "Done.",
            },
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 3, "completion_tokens": 5},
    }

    result = openai_to_anthropic_response(body, "logical-model")

    assert result["content"] == [
        {"type": "thinking", "thinking": "I should inspect the route first."},
        {"type": "text", "text": "Done."},
    ]


def test_anthropic_to_openai_response_preserves_thinking_block():
    body = {
        "id": "msg_123",
        "model": "claude-test",
        "content": [
            {"type": "thinking", "thinking": "I should inspect the route first."},
            {"type": "text", "text": "Done."},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 3, "output_tokens": 5},
    }

    result = anthropic_to_openai_response(body)
    message = result["choices"][0]["message"]

    assert message["reasoning_content"] == "I should inspect the route first."
    assert message["content"] == "Done."


def test_anthropic_to_openai_request_preserves_assistant_thinking_block():
    payload = {
        "model": "claude-test",
        "messages": [{
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "I should inspect the route first."},
                {"type": "text", "text": "Done."},
            ],
        }],
    }

    result = anthropic_to_openai_request(payload, "openai-test")

    assert result["messages"] == [{
        "role": "assistant",
        "content": "Done.",
        "reasoning_content": "I should inspect the route first.",
        "partial": True,
    }]


def test_openai_to_anthropic_request_preserves_assistant_reasoning_content():
    payload = {
        "model": "openai-test",
        "messages": [{
            "role": "assistant",
            "reasoning_content": "I should inspect the route first.",
            "content": "Done.",
        }],
    }

    result = openai_to_anthropic_request(payload, "claude-test")

    assert result["messages"] == [{
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "I should inspect the route first."},
            {"type": "text", "text": "Done."},
        ],
    }]
