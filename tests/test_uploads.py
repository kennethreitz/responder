"""v5.5: Form()/File() markers, typed UploadFile, and multipart OpenAPI."""

import yaml
from starlette.testclient import TestClient

import responder
from responder import File, Form, UploadFile


def _api():
    return responder.API(
        title="T", version="1", openapi="3.0.2", secret_key="x" * 32,
        allowed_hosts=[";"], session_https_only=False,
    )


def _client(api):
    return TestClient(api, base_url="http://;")


def test_file_and_form_injection():
    api = _api()

    @api.post("/upload")
    async def upload(
        req, resp, *, f: UploadFile = File(...), name: str = Form(...), count: int = Form(1)
    ):
        body = await f.read()
        resp.media = {
            "filename": f.filename,
            "content": body.decode(),
            "name": name,
            "count": count,
        }

    r = _client(api).post(
        "/upload",
        files={"f": ("test.txt", b"hello world", "text/plain")},
        data={"name": "widget", "count": "5"},
    )
    assert r.status_code == 200
    assert r.json() == {
        "filename": "test.txt",
        "content": "hello world",
        "name": "widget",
        "count": 5,  # coerced
    }


def test_multiple_files():
    api = _api()

    @api.post("/multi")
    async def multi(req, resp, *, files: list[UploadFile] = File(...)):
        resp.media = {"names": [f.filename for f in files]}

    r = _client(api).post(
        "/multi",
        files=[
            ("files", ("a.txt", b"aaa", "text/plain")),
            ("files", ("b.txt", b"bb", "text/plain")),
        ],
    )
    assert r.json() == {"names": ["a.txt", "b.txt"]}


def test_required_file_missing_returns_422():
    api = _api()

    @api.post("/upload")
    async def upload(req, resp, *, f: UploadFile = File(...)):
        resp.media = {}

    assert _client(api).post("/upload", data={"x": "1"}).status_code == 422


def test_optional_file_absent():
    api = _api()

    @api.post("/upload")
    async def upload(req, resp, *, f: UploadFile = File(None)):
        resp.media = {"got": f is not None}

    assert _client(api).post("/upload", data={}).json() == {"got": False}


def test_annotated_file_form():
    from typing import Annotated

    api = _api()

    @api.post("/u")
    async def u(req, resp, *, f: Annotated[UploadFile, File()], tag: Annotated[str, Form()]):
        resp.media = {"filename": f.filename, "tag": tag}

    r = _client(api).post(
        "/u", files={"f": ("x.txt", b"x", "text/plain")}, data={"tag": "v"}
    )
    assert r.json() == {"filename": "x.txt", "tag": "v"}


def test_legacy_files_media_still_works():
    api = _api()

    @api.route("/legacy", methods=["POST"])
    async def legacy(req, resp):
        files = await req.media("files")
        resp.media = {"keys": sorted(files.keys())}

    r = _client(api).post(
        "/legacy", files={"doc": ("x.bin", b"\x00\x01", "application/octet-stream")}
    )
    assert r.json() == {"keys": ["doc"]}


def test_multipart_openapi_request_body():
    api = _api()

    @api.post("/upload")
    async def upload(req, resp, *, f: UploadFile = File(...), name: str = Form(...)):
        resp.media = {}

    spec = yaml.safe_load(_client(api).get("/schema.yml").content)
    rb = spec["paths"]["/upload"]["post"]["requestBody"]
    schema = rb["content"]["multipart/form-data"]["schema"]
    assert schema["properties"]["f"] == {"type": "string", "format": "binary"}
    assert schema["properties"]["name"]["type"] == "string"
    assert set(schema["required"]) == {"f", "name"}


def test_urlencoded_openapi_when_no_file():
    api = _api()

    @api.post("/form")
    async def form_only(req, resp, *, name: str = Form(...)):
        resp.media = {"name": name}

    # urlencoded form submission injects the field
    r = _client(api).post("/form", data={"name": "alice"})
    assert r.json() == {"name": "alice"}

    spec = yaml.safe_load(_client(api).get("/schema.yml").content)
    rb = spec["paths"]["/form"]["post"]["requestBody"]
    assert "application/x-www-form-urlencoded" in rb["content"]
