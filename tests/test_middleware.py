from llm_router.middleware import _should_skip_request_logging


def test_request_logging_skips_health_check():
    assert _should_skip_request_logging("/healthz") is True


def test_request_logging_skips_static_assets():
    assert _should_skip_request_logging("/static") is True
    assert _should_skip_request_logging("/static/i18n/zh.js") is True


def test_request_logging_keeps_application_routes():
    assert _should_skip_request_logging("/") is False
    assert _should_skip_request_logging("/admin") is False
    assert _should_skip_request_logging("/v1/chat/completions") is False
