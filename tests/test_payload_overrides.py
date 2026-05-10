from decimal import Decimal

from llm_router.domain.enums import ProviderProtocol
from llm_router.domain.schemas import RoutedProvider
from llm_router.services.non_stream_handlers.openai import OpenAINonStreamHandler
from llm_router.services.payload_overrides import deep_merge_payload


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

    result = deep_merge_payload(payload, overrides)

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

    result = deep_merge_payload(payload, overrides)

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
