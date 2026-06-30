import asyncio

import pytest
import yaml
from pydantic import BaseModel
from starlette.testclient import TestClient

import responder
from responder.ext.auth import BearerAuth
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


def _assert_problem_shape(response, expected, *, errors=False):
    assert response.status_code == expected["status"]
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    for key, value in expected.items():
        assert body[key] == value
    if errors:
        assert isinstance(body["errors"], list)
        assert body["errors"]
    else:
        assert "errors" not in body
    assert set(body) <= {*expected, "errors"}


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


def _problem_case_response(name, *, problem_details=True):
    accept_json = {"Accept": "application/json"} if not problem_details else {}
    if name == "400":
        class Item(BaseModel):
            name: str

        api = _api(problem_details=problem_details)

        @api.post("/items")
        def create_item(req, resp, *, item: Item):
            resp.media = item.model_dump()

        return _client(api).post(
            "/items",
            content="{",
            headers={"Content-Type": "application/json", **accept_json},
        )

    if name == "401":
        api = _api(auth=BearerAuth(tokens=["secret"]), problem_details=problem_details)

        @api.get("/private")
        def private(req, resp, *, user):
            resp.media = {"user": user}

        return _client(api).get("/private", headers=accept_json)

    if name == "403":
        auth = BearerAuth(verify=lambda token: {"scopes": []}).requires("admin")
        api = _api(auth=auth, problem_details=problem_details)

        @api.get("/admin")
        def admin(req, resp, *, user):
            resp.media = {"user": user}

        return _client(api).get(
            "/admin",
            headers={"Authorization": "Bearer t", **accept_json},
        )

    if name == "404":
        return _client(_api(problem_details=problem_details)).get(
            "/missing", headers=accept_json
        )

    if name == "405":
        api = _api(problem_details=problem_details)

        @api.get("/items")
        def items(req, resp):
            resp.media = {"ok": True}

        return _client(api).post("/items", headers=accept_json)

    if name == "413":
        api = _api(max_request_size=10, problem_details=problem_details)

        @api.post("/upload")
        async def upload(req, resp):
            await req.content
            resp.text = "ok"

        return _client(api).post(
            "/upload", content=b"x" * 100, headers=accept_json
        )

    if name == "422":
        api = _api(problem_details=problem_details)

        @api.get("/items/{id}")
        def item(req, resp, *, id: int):
            resp.media = {"id": id}

        return _client(api).get("/items/not-an-int", headers=accept_json)

    if name == "500":
        api = _api(problem_details=problem_details)

        @api.get("/boom")
        def boom(req, resp):
            raise RuntimeError("boom")

        return _client(api).get("/boom", headers=accept_json)

    if name == "504":
        api = _api(request_timeout=0.01, problem_details=problem_details)

        @api.get("/slow")
        async def slow(req, resp):
            await asyncio.sleep(1)

        return _client(api).get("/slow", headers=accept_json)

    raise AssertionError(f"unknown problem-details case: {name}")


@pytest.mark.parametrize(
    ("name", "expected", "errors"),
    [
        (
            "400",
            {
                "type": "about:blank",
                "title": "Bad Request",
                "status": 400,
                "detail": "Invalid JSON body",
            },
            False,
        ),
        (
            "401",
            {
                "type": "about:blank",
                "title": "Unauthorized",
                "status": 401,
                "detail": "Not authenticated",
            },
            False,
        ),
        (
            "403",
            {
                "type": "about:blank",
                "title": "Forbidden",
                "status": 403,
                "detail": "Insufficient scope: admin",
            },
            False,
        ),
        (
            "404",
            {
                "type": "about:blank",
                "title": "Not Found",
                "status": 404,
                "detail": "Not Found",
            },
            False,
        ),
        (
            "405",
            {"type": "about:blank", "title": "Method Not Allowed", "status": 405},
            False,
        ),
        (
            "413",
            {
                "type": "about:blank",
                "title": "Content Too Large",
                "status": 413,
                "detail": "Request body too large",
            },
            False,
        ),
        (
            "422",
            {
                "type": "about:blank",
                "title": "Validation Error",
                "status": 422,
                "detail": "Validation failed",
            },
            True,
        ),
        (
            "500",
            {
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "Internal Server Error",
            },
            False,
        ),
        (
            "504",
            {
                "type": "about:blank",
                "title": "Gateway Timeout",
                "status": 504,
                "detail": "Request timed out",
            },
            False,
        ),
    ],
)
def test_framework_problem_details_golden_shapes(name, expected, errors):
    _assert_problem_shape(
        _problem_case_response(name),
        expected,
        errors=errors,
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("400", {"error": "Invalid JSON body"}),
        ("401", {"error": "Not authenticated"}),
        ("403", {"error": "Insufficient scope: admin"}),
        ("404", {"error": "Not Found"}),
        ("405", {"error": "Method Not Allowed"}),
        ("413", {"error": "Request body too large"}),
        ("500", {"error": "Internal Server Error"}),
        ("504", {"error": "Request timed out"}),
    ],
)
def test_framework_legacy_error_golden_shapes(name, expected):
    response = _problem_case_response(name, problem_details=False)

    assert response.status_code in {400, 401, 403, 404, 405, 413, 500, 504}
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == expected


def test_framework_legacy_validation_error_golden_shape():
    response = _problem_case_response("422", problem_details=False)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert set(response.json()) == {"errors"}
    assert response.json()["errors"]
