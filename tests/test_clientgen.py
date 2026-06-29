"""Client generation from OpenAPI."""

import importlib.util

import pytest
from pydantic import BaseModel

import responder
from responder import Path, Query


def _load_module(path):
    spec = importlib.util.spec_from_file_location("generated_client", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ItemIn(BaseModel):
    name: str


class ItemOut(BaseModel):
    id: int
    name: str


def _api():
    api = responder.API(
        title="Service",
        version="1",
        openapi="3.0.2",
        allowed_hosts=[";"],
        session_https_only=False,
    )

    @api.get("/users/{user_id}", operation_id="get_user")
    def get_user(
        req,
        resp,
        *,
        user_id: int = Path(...),
        include_details: bool = Query(False),
    ):
        resp.media = {"id": user_id, "details": include_details}

    @api.post(
        "/items",
        operation_id="create_item",
        request_model=ItemIn,
        response_model=ItemOut,
    )
    async def create_item(req, resp):
        item = await req.media()
        resp.media = {"id": 1, **item}

    @api.get("/boom", operation_id="boom")
    def boom(req, resp):
        resp.status_code = 418
        resp.media = {"error": "teapot"}

    return api


def test_generate_client_source_and_call_in_process_session(tmp_path):
    api = _api()
    path = tmp_path / "service_client.py"
    written = api.generate_client(path, class_name="ServiceClient")
    assert written == path

    module = _load_module(path)
    client = module.ServiceClient(session=api.requests)

    assert client.get_user(7, include_details=True) == {
        "id": 7,
        "details": True,
    }
    assert client.create_item(body={"name": "tea"}) == {"id": 1, "name": "tea"}

    with pytest.raises(module.APIError) as excinfo:
        client.boom()
    assert excinfo.value.status_code == 418
    assert excinfo.value.body == {"error": "teapot"}


def test_generate_client_returns_source():
    api = _api()
    source = api.generate_client(class_name="ServiceClient")
    assert "class ServiceClient" in source
    assert "def get_user(self, user_id: int" in source
    assert "def create_item(self, body: dict[str, Any] | None = None)" in source


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        (
            "javascript",
            [
                "export class ServiceClient",
                "get_user(userId, includeDetails = null)",
                "create_item(body = null)",
                "fetchImpl",
            ],
        ),
        (
            "typescript",
            [
                "export class ServiceClient",
                "get_user(userId: number, includeDetails: boolean | null = null)",
                "create_item(body: Record<string, unknown> | null = null)",
                "Promise<unknown>",
            ],
        ),
        (
            "ruby",
            [
                "class ServiceClient",
                "def get_user(user_id, include_details: nil)",
                "def create_item(body: nil)",
                "Net::HTTP",
            ],
        ),
        (
            "php",
            [
                "class ServiceClient",
                "public function get_user($user_id, $include_details = null): mixed",
                "public function create_item($body = null): mixed",
                "file_get_contents",
            ],
        ),
    ],
)
def test_generate_client_supports_other_languages(language, expected):
    api = _api()
    source = api.generate_client(class_name="ServiceClient", language=language)

    for snippet in expected:
        assert snippet in source


def test_generate_client_writes_other_language(tmp_path):
    api = _api()
    path = tmp_path / "service_client.ts"
    written = api.generate_client(
        path, class_name="ServiceClient", language="typescript"
    )

    assert written == path
    assert "export class ServiceClient" in path.read_text()


def test_generate_client_rejects_unknown_language():
    api = _api()
    with pytest.raises(ValueError, match="language must be one of"):
        api.generate_client(class_name="ServiceClient", language="elvish")


def test_generate_client_requires_openapi():
    api = responder.API(allowed_hosts=[";"], session_https_only=False)
    with pytest.raises(RuntimeError, match="OpenAPI is not enabled"):
        api.generate_client()
