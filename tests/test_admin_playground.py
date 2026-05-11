import json

from fastapi import HTTPException

from llm_router.api.admin import _playground_http_exception_response


def test_playground_http_exception_response_preserves_json_detail():
    upstream_body = {
        "error": {
            "message": "Inference failed",
            "type": "BadRequest",
            "param": "",
            "code": "ModelArts.81001",
        }
    }

    response = _playground_http_exception_response(
        HTTPException(status_code=400, detail=json.dumps(upstream_body))
    )

    assert response.status_code == 400
    assert json.loads(response.body) == upstream_body
    assert "ok" not in json.loads(response.body)


def test_playground_http_exception_response_wraps_plain_text_without_ok_flag():
    response = _playground_http_exception_response(
        HTTPException(status_code=503, detail="No provider succeeded")
    )

    assert response.status_code == 503
    assert json.loads(response.body) == {"error": "No provider succeeded"}
