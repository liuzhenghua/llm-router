import pytest
from fastapi import HTTPException, status

from llm_router.services.protocol_converter import anthropic_to_openai_request


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
