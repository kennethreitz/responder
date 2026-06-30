import asyncio

import pytest
import yaml
from pydantic import BaseModel
from starlette.testclient import TestClient

import responder
from responder.testing import assert_problem


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


def test_problem_handler_can_enrich_payload():
    def problem_handler(payload, request, exc):
        payload["type"] = "https://example.test/problems/" + str(payload["status"])
        payload["instance"] = request.url.path
        payload["code"] = "E_NOT_FOUND"

    api = _api(problem_handler=problem_handler)

    response = _client(api).get("/missing")

    body = assert_problem(
        response,
        404,
        type="https://example.test/problems/404",
        instance="/missing",
        code="E_NOT_FOUND",
    )
    assert body["title"] == "Not Found"


def test_problem_details_include_request_id_when_enabled():
    api = _api(request_id=True)

    response = _client(api).get("/missing", headers={"X-Request-ID": "req-123"})

    assert_problem(response, 404, request_id="req-123")
    assert response.headers["x-request-id"] == "req-123"


def test_openapi_documents_problem_responses():
    api = _api(openapi="3.0.2", request_timeout=10)

    class Item(BaseModel):
        name: str

    @api.post("/items", response_model=Item)
    def create_item(req, resp, *, item: Item):
        resp.media = item.model_dump()

    spec = yaml.safe_load(_client(api).get("/schema.yml").content)
    operation = spec["paths"]["/items"]["post"]

    assert operation["operationId"] == "post_items"
    assert operation["summary"] == "Post Items"
    assert operation["tags"] == ["Items"]
    assert spec["components"]["schemas"]["ProblemDetails"]["required"] == [
        "type",
        "title",
        "status",
    ]
    for status in ("400", "404", "405", "413", "422", "500", "504"):
        content = operation["responses"][status]["content"]
        assert "application/problem+json" in content


def test_openapi_documents_legacy_errors_when_problem_details_false():
    api = _api(openapi="3.0.2", problem_details=False)

    @api.get("/items")
    def items(req, resp):
        resp.media = {"ok": True}

    spec = yaml.safe_load(_client(api).get("/schema.yml").content)
    schemas = spec.get("components", {}).get("schemas", {})
    operation = spec["paths"]["/items"]["get"]

    assert "ProblemDetails" not in schemas
    assert "application/problem+json" not in operation["responses"]["404"]["content"]
    assert "application/json" in operation["responses"]["404"]["content"]


def test_problem_handler_receives_framework_exception():
    seen = {}

    def problem_handler(payload, request, exc):
        seen["exc"] = exc

    class Out(BaseModel):
        id: int

    api = _api(problem_handler=problem_handler)

    @api.get("/items", response_model=Out)
    def items(req, resp):
        resp.media = {"id": "bad"}

    response = _client(api).get("/items")

    assert response.status_code == 500
    assert seen["exc"] is not None
    assert hasattr(seen["exc"], "errors")


def test_problem_handler_failure_falls_back_to_original_payload():
    def problem_handler(payload, request, exc):
        raise RuntimeError("handler broke")

    api = _api(problem_handler=problem_handler)

    response = _client(api).get("/missing")

    _assert_problem(response, 404, "Not Found")
