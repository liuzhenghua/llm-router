from decimal import Decimal

from llm_router.domain.enums import ProviderProtocol
from llm_router.domain.schemas import RoutedProvider
from llm_router.services.non_stream_handlers.openai import OpenAINonStreamHandler
from llm_router.services.payload_overrides import (
    IMAGE_REMOVED_TOOL_RESULT_TEXT,
    _deep_merge_payload,
    strip_image_content_from_payload,
)


def _provider(**overrides) -> RoutedProvider:
    values = {
        "id": 1,
        "name": "provider",
        "protocol": ProviderProtocol.OPENAI,
        "upstream_protocol": ProviderProtocol.OPENAI,
        "endpoint": "https://example.com/v1",
        "api_key": "secret",
        "upstream_model_name": "upstream-model",
        "timeout_seconds": 30,
        "input_token_price": Decimal("0"),
        "output_token_price": Decimal("0"),
        "cache_read_token_price": Decimal("0"),
        "cache_write_token_price": Decimal("0"),
        "supports_prompt_cache": False,
    }
    values.update(overrides)
    return RoutedProvider(**values)


def test_deep_merge_payload_preserves_nested_request_fields():
    payload = {
        "model": "logical-model",
        "chat_template_kwargs": {"tools": True},
    }
    overrides = {
        "thinking": {"type": "disabled"},
        "chat_template_kwargs": {"thinking": False},
    }

    result = _deep_merge_payload(payload, overrides)

    assert result == {
        "model": "logical-model",
        "thinking": {"type": "disabled"},
        "chat_template_kwargs": {"tools": True, "thinking": False},
    }
    assert payload == {
        "model": "logical-model",
        "chat_template_kwargs": {"tools": True},
    }


def test_deep_merge_payload_preserves_non_json_scalar_types():
    payload = {
        "model": "logical-model",
        "metadata": {"price": Decimal("0.10")},
    }
    overrides = {
        "metadata": {"limit": Decimal("1.25")},
    }

    result = _deep_merge_payload(payload, overrides)

    assert result["metadata"]["price"] == Decimal("0.10")
    assert result["metadata"]["limit"] == Decimal("1.25")
    assert isinstance(result["metadata"]["price"], Decimal)
    assert isinstance(result["metadata"]["limit"], Decimal)
    assert "limit" not in payload["metadata"]


def test_openai_handler_applies_protocol_payload_overrides_after_model_patch():
    provider = _provider(
        openai_payload_overrides={
            "thinking": {"type": "disabled"},
            "chat_template_kwargs": {"thinking": False},
            "max_tokens": 1024,
        }
    )

    result = OpenAINonStreamHandler().prepare_payload(
        {"model": "logical-model", "chat_template_kwargs": {"foo": "bar"}},
        provider,
    )

    assert result["model"] == "upstream-model"
    assert result["thinking"] == {"type": "disabled"}
    assert result["chat_template_kwargs"] == {"foo": "bar", "thinking": False}
    assert result["max_tokens"] == 1024


def test_strip_image_content_removes_openai_image_parts_and_keeps_text():
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                    {"type": "input_image", "image_url": "https://example.com/a.png"},
                ],
            }
        ]
    }

    result = strip_image_content_from_payload(payload)

    assert result["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "describe this"}]}
    ]
    assert len(payload["messages"][0]["content"]) == 3


def test_strip_image_content_drops_image_only_message():
    payload = {
        "messages": [
            {"role": "system", "content": "You are concise."},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]},
            {"role": "user", "content": "hello"},
        ]
    }

    result = strip_image_content_from_payload(payload)

    assert result["messages"] == [
        {"role": "system", "content": "You are concise."},
        {"role": "user", "content": "hello"},
    ]


def test_strip_image_content_removes_anthropic_image_blocks():
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abc"}},
                    {"type": "text", "text": "what is this?"},
                ],
            }
        ]
    }

    result = strip_image_content_from_payload(payload)

    assert result["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "what is this?"}]}
    ]


def test_strip_image_content_replaces_image_only_tool_result_with_text_placeholder():
    payload = {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "让我先看一下截图来理解UI设计。"},
                    {
                        "type": "tool_use",
                        "id": "call_2030334446564e4d9d53841e",
                        "name": "Read",
                        "input": {"file_path": "D:\\tmp\\temp.png"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "tool_use_id": "call_2030334446564e4d9d53841e",
                        "type": "tool_result",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "data": "iVBORw0KGgoAAAAN",
                                    "media_type": "image/png",
                                },
                            }
                        ],
                    }
                ],
            },
        ]
    }

    result = strip_image_content_from_payload(payload)

    tool_result = result["messages"][1]["content"][0]
    assert tool_result["tool_use_id"] == "call_2030334446564e4d9d53841e"
    assert tool_result["content"] == [{"type": "text", "text": IMAGE_REMOVED_TOOL_RESULT_TEXT}]


def test_strip_image_content_keeps_text_in_mixed_tool_result():
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "tool_use_id": "call_1",
                        "type": "tool_result",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "data": "abc", "media_type": "image/png"}},
                            {"type": "text", "text": "OCR result"},
                        ],
                    }
                ],
            }
        ]
    }

    result = strip_image_content_from_payload(payload)

    assert result["messages"][0]["content"][0]["content"] == [{"type": "text", "text": "OCR result"}]


def test_provider_strip_image_content_runs_before_payload_overrides():
    provider = _provider(
        strip_image_content=True,
        openai_payload_overrides={"max_tokens": 1024},
    )

    result = OpenAINonStreamHandler().prepare_payload(
        {
            "model": "logical-model",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                    ],
                }
            ],
        },
        provider,
    )

    assert result["model"] == "upstream-model"
    assert result["messages"][0]["content"] == [{"type": "text", "text": "hello"}]
    assert result["max_tokens"] == 1024
