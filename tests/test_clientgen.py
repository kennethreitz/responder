"""Client generation from OpenAPI."""

import importlib.util
import shutil
import subprocess

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


def test_generated_python_client_validates_request_body(tmp_path):
    api = _api()
    path = tmp_path / "service_client.py"
    api.generate_client(path, class_name="ServiceClient")
    module = _load_module(path)
    client = module.ServiceClient(session=api.requests, validate=True)

    with pytest.raises(module.APIValidationError) as excinfo:
        client.create_item(body={"name": 123})

    assert str(excinfo.value) == "body.name expected string"
    assert excinfo.value.path == "body.name"
    assert excinfo.value.expected == "string"


def test_generated_python_client_validates_response_body(tmp_path):
    api = _api()
    path = tmp_path / "service_client.py"
    api.generate_client(path, class_name="ServiceClient")
    module = _load_module(path)

    class Response:
        status_code = 200
        headers = {"content-type": "application/json"}
        content = b'{"id": "bad", "name": "tea"}'

    class Session:
        def request(self, *args, **kwargs):
            return Response()

    client = module.ServiceClient(session=Session(), validate=True)
    with pytest.raises(module.APIValidationError) as excinfo:
        client.create_item(body={"name": "tea"})

    assert str(excinfo.value) == "response.id expected integer"
    assert excinfo.value.path == "response.id"
    assert excinfo.value.value == "bad"


def test_generate_client_returns_source():
    api = _api()
    source = api.generate_client(class_name="ServiceClient")
    assert "class ServiceClient" in source
    assert "def get_user(self, user_id: int" in source
    assert "class ItemIn(TypedDict):" in source
    assert "class ItemOut(TypedDict):" in source
    assert "class APIValidationError(Exception):" in source
    assert "validate: bool = False" in source
    assert "def create_item(self, body: ItemIn | None = None) -> ItemOut" in source


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
                "APIValidationError",
                "validate = false",
            ],
        ),
        (
            "typescript",
            [
                "export class ServiceClient",
                "get_user(userId: number, includeDetails: boolean | null = null)",
                "export interface ItemIn",
                "export interface ItemOut",
                "create_item(body: ItemIn | null = null): Promise<ItemOut>",
                "export class APIValidationError",
                "responseSchema",
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


def test_generated_typescript_validator_is_annotated():
    """The TS validation helpers must be type-annotated (noImplicitAny)."""
    source = _api().generate_client(class_name="ServiceClient", language="typescript")
    assert "const SCHEMAS: Record<string, any>" in source
    assert "const validateValue = (value: any, schema: any, path: string" in source
    assert "const resolveSchema = (schema: any)" in source
    assert "variants.some((variant: any)" in source


@pytest.mark.parametrize(
    ("language", "suffix", "command"),
    [
        ("javascript", ".mjs", ("node", "--check")),
        ("typescript", ".ts", ("deno", "check")),
        ("ruby", ".rb", ("ruby", "-c")),
        ("php", ".php", ("php", "-l")),
    ],
)
def test_generated_client_passes_native_syntax_check(
    language, suffix, command, tmp_path
):
    """Each generated client parses/type-checks with its own language's tool.

    Skipped when the toolchain isn't installed; CI installs all four so the
    generated JS/TS/Ruby/PHP are verified before release, not after.
    """
    tool = shutil.which(command[0])
    if tool is None:
        pytest.skip(f"{command[0]} not installed")
    path = tmp_path / f"client{suffix}"
    _api().generate_client(str(path), class_name="ServiceClient", language=language)
    result = subprocess.run(  # noqa: S603 - tool path + temp file are test-controlled
        [tool, *command[1:], str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
