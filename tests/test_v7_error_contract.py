import asyncio

import pytest
from pydantic import BaseModel
from starlette.testclient import TestClient

import responder


def _api(**kwargs):
    return responder.API(
        title="T",
        version="1",
        secret_key="x" * 32,
        allowed_hosts=[";"],
        session_https_only=False,
        **kwargs,
    )


def _client(api):
    return TestClient(api, base_url="http://;", raise_server_exceptions=False)


def _assert_problem(response, status, title, *, detail=None):
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"] == title
    assert body["status"] == status
    if detail is not None:
        assert body["detail"] == detail
    return body


@pytest.mark.parametrize(
    ("request_path", "request_method", "status", "title"),
    [
        ("/missing", "get", 404, "Not Found"),
        ("/items", "post", 405, "Method Not Allowed"),
        ("/items/not-an-int", "get", 422, "Validation Error"),
        ("/boom", "get", 500, "Internal Server Error"),
    ],
)
def test_problem_details_error_contract(request_path, request_method, status, title):
    api = _api()

    @api.get("/items")
    def items(req, resp):
        resp.media = {"ok": True}

    @api.get("/items/{id}")
    def item(req, resp, *, id: int):
        resp.media = {"id": id}

    @api.get("/boom")
    def boom(req, resp):
        raise RuntimeError("boom")

    response = getattr(_client(api), request_method)(request_path)
    body = _assert_problem(response, status, title)
    if status == 422:
        assert "errors" in body


def test_timeout_uses_problem_details_contract():
    api = _api(request_timeout=0.01)

    @api.get("/slow")
    async def slow(req, resp):
        await asyncio.sleep(1)

    response = _client(api).get("/slow")
    _assert_problem(response, 504, "Gateway Timeout", detail="Request timed out")


def test_problem_details_false_keeps_legacy_json_error_shape():
    api = _api(problem_details=False)

    response = _client(api).get("/missing", headers={"Accept": "application/json"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"error": "Not Found"}


def test_problem_details_force_json_bytes_for_yaml_accept():
    class Out(BaseModel):
        id: int

    api = _api()

    @api.get("/items", response_model=Out)
    def items(req, resp):
        resp.media = {"id": "bad"}

    response = _client(api).get("/items", headers={"Accept": "application/yaml"})

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.content.lstrip().startswith(b"{")
    assert response.json()["status"] == 500


def test_response_model_validation_failure_uses_problem_details():
    class Out(BaseModel):
        id: int

    api = _api()

    @api.get("/items", response_model=Out)
    def items(req, resp):
        resp.media = {"id": "bad"}

    body = _assert_problem(
        _client(api).get("/items"),
        500,
        "Internal Server Error",
        detail="Internal Server Error",
    )
    assert "errors" in body


def test_after_hook_failure_uses_problem_details():
    api = _api()

    def broken(req, resp):
        raise RuntimeError("broken")

    @api.get("/items", after=broken)
    def items(req, resp):
        resp.media = {"ok": True}

    _assert_problem(
        _client(api).get("/items"),
        500,
        "Internal Server Error",
        detail="Internal Server Error",
    )
