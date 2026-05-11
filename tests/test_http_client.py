import httpx

from llm_router.services.http_client import upstream_error_detail


def test_upstream_error_detail_uses_exception_class_when_message_is_empty():
    assert upstream_error_detail(httpx.ConnectTimeout("")) == "ConnectTimeout"


def test_upstream_error_detail_prefers_exception_message():
    assert upstream_error_detail(RuntimeError("boom")) == "boom"
