"""8.0.1 OpenAPI/clientgen regression tests.

Covers: dangling parameter $refs (56), header/cookie parameters dropped by
generated clients (57), form/multipart bodies sent as JSON (58), WebSocket
routes documented as GET (59), docs_route without openapi= (60), missing
requestBody.required (61), and parameter-name collisions in generated code (68).

Review follow-ups: a user route at the implied schema path must not crash (A),
same-named hoisted $defs must not merge (B), a JS/TS parameter named ``quote``
must not shadow the URL-encoding helper (C), Ruby multipart must send file
parts with filenames (D), and PHP must only treat strict file specs as files (E).
"""

import enum
import importlib.util

import pytest
import yaml
from openapi_spec_validator import validate
from pydantic import BaseModel

import responder
from responder import Cookie, File, Form, Header, Query
from responder.ext.clientgen import generate_client


def _load_module(path):
    spec = importlib.util.spec_from_file_location("generated_client_801", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _api(**kwargs):
    kwargs.setdefault("openapi", "3.1.0")
    return responder.API(
        title="T",
        version="1",
        allowed_hosts=[";"],
        session_https_only=False,
        **kwargs,
    )


def _spec_of(api):
    return yaml.safe_load(api.openapi.openapi)


def _collect_refs(obj, refs):
    if isinstance(obj, dict):
        ref = obj.get("$ref")
        if isinstance(ref, str):
            refs.append(ref)
        for value in obj.values():
            _collect_refs(value, refs)
    elif isinstance(obj, list):
        for item in obj:
            _collect_refs(item, refs)
    return refs


class Color(enum.Enum):
    red = "red"
    blue = "blue"


class PaintParams(BaseModel):
    color: Color = Color.red


# ---------------------------------------------------------------------------
# Item 56: parameter schemas must not emit dangling $refs / inline $defs.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("openapi_version", ["3.0.2", "3.1.0"])
def test_enum_parameters_register_components_and_spec_validates(openapi_version):
    api = _api(openapi=openapi_version)

    @api.get("/paint", params_model=PaintParams)
    def paint(req, resp, *, params):
        resp.media = {"color": params.color.value}

    @api.get("/items")
    def items(req, resp, *, colors: list[Color] = Query([])):
        resp.media = {"count": len(colors)}

    spec = _spec_of(api)
    schemas = spec["components"]["schemas"]
    assert "Color" in schemas

    # Every $ref in the document must resolve to a registered component.
    prefix = "#/components/schemas/"
    for ref in _collect_refs(spec, []):
        assert ref.startswith(prefix)
        assert ref.removeprefix(prefix) in schemas, f"dangling ref: {ref}"

    # No parameter schema carries an inline $defs block.
    assert "$defs" not in str(spec)

    # And the whole document is a valid OpenAPI spec.
    validate(spec)


def test_enum_header_and_form_parameters_hoist_defs():
    api = _api()

    @api.get("/hdr")
    def hdr(req, resp, *, shades: list[Color] = Header([])):
        resp.media = {"count": len(shades)}

    @api.post("/pick")
    async def pick(req, resp, *, favorites: list[Color] = Form(...)):
        resp.media = {"count": len(favorites)}

    spec = _spec_of(api)
    assert "Color" in spec["components"]["schemas"]
    assert "$defs" not in str(spec)
    validate(spec)


# ---------------------------------------------------------------------------
# Item 59: WebSocket routes are not HTTP operations.
# ---------------------------------------------------------------------------


def test_websocket_routes_are_not_documented_as_get():
    api = _api()

    @api.get("/http")
    def http(req, resp):
        resp.media = {"ok": True}

    @api.websocket_route("/ws")
    async def ws(ws):  # pragma: no cover - never connected
        await ws.accept()
        await ws.close()

    spec = _spec_of(api)
    assert "/http" in spec["paths"]
    assert "/ws" not in spec["paths"]
    validate(spec)


# ---------------------------------------------------------------------------
# Item 60: docs_route without openapi= serves a working schema + docs page.
# ---------------------------------------------------------------------------


def test_docs_route_without_openapi_serves_schema_and_docs():
    api = responder.API(
        docs_route="/docs/", allowed_hosts=[";"], session_https_only=False
    )

    @api.get("/hello")
    def hello(req, resp):
        resp.media = {"hello": "world"}

    # The docstring promises docs_route "Enables OpenAPI if not already set".
    schema = api.requests.get("http://;/schema.yml")
    assert schema.status_code == 200
    spec = yaml.safe_load(schema.text)
    assert spec["openapi"].startswith("3.1")
    assert "/hello" in spec["paths"]
    validate(spec)

    docs = api.requests.get("http://;/docs/")
    assert docs.status_code == 200

    # api.openapi.openapi used to raise TypeError inside apispec.
    assert "openapi" in api.openapi.openapi


# ---------------------------------------------------------------------------
# Item 61: required bodies are marked required:true.
# ---------------------------------------------------------------------------


class ItemIn(BaseModel):
    name: str


def test_request_body_marked_required():
    api = _api()

    @api.post("/things")
    async def things(req, resp, *, item: ItemIn):
        resp.media = {"ok": True}

    @api.post("/upload")
    async def upload(req, resp, *, name: str = Form(...), data=File(...)):
        resp.media = {"ok": True}

    @api.post("/optional-form")
    async def optional_form(req, resp, *, note: str = Form("hi")):
        resp.media = {"ok": True}

    spec = _spec_of(api)
    assert spec["paths"]["/things"]["post"]["requestBody"]["required"] is True
    assert spec["paths"]["/upload"]["post"]["requestBody"]["required"] is True
    # An all-optional form body stays optional.
    optional = spec["paths"]["/optional-form"]["post"]["requestBody"]
    assert "required" not in optional
    validate(spec)


def test_generated_client_requires_required_body():
    api = _api()

    @api.post("/things", operation_id="create_thing")
    async def things(req, resp, *, item: ItemIn):
        resp.media = {"ok": True}

    source = generate_client(api)
    assert "def create_thing(self, body: ItemIn) -> Any:" in source


# ---------------------------------------------------------------------------
# Item 57: generated clients send header/cookie parameters.
# ---------------------------------------------------------------------------


def _header_cookie_api():
    api = _api()

    @api.get("/whoami", operation_id="whoami")
    def whoami(
        req,
        resp,
        *,
        x_token: str = Header(...),
        session_id: str = Cookie(...),
    ):
        resp.media = {"token": x_token, "session": session_id}

    return api


def test_python_client_sends_header_and_cookie_parameters(tmp_path):
    api = _header_cookie_api()
    path = tmp_path / "client.py"
    api.generate_client(path, class_name="Client")
    client = _load_module(path).Client(session=api.requests)

    assert client.whoami(x_token="tok", session_id="abc") == {
        "token": "tok",
        "session": "abc",
    }


def test_all_languages_pass_header_and_cookie_parameters():
    api = _header_cookie_api()
    py = generate_client(api)
    assert "headers={'x-token': x_token}" in py
    assert "cookies={'session_id': session_id}" in py
    js = generate_client(api, language="javascript")
    assert "headers: {'x-token': xToken}" in js
    assert "cookies: {'session_id': sessionId}" in js
    ts = generate_client(api, language="typescript")
    assert "headers: {'x-token': xToken}" in ts
    rb = generate_client(api, language="ruby")
    assert "headers: {'x-token' => x_token}" in rb
    assert "cookies: {'session_id' => session_id}" in rb
    assert "req['Cookie'] = cookie" in rb
    php = generate_client(api, language="php")
    assert "headers: ['x-token' => $x_token]" in php
    assert "cookies: ['session_id' => $session_id]" in php
    assert "$headers['Cookie'] = $cookie;" in php


# ---------------------------------------------------------------------------
# Item 58: form/multipart bodies are sent as forms, not JSON.
# ---------------------------------------------------------------------------


def _form_api():
    api = _api()

    @api.post("/form", operation_id="submit_form")
    async def form(req, resp, *, name: str = Form(...), count: int = Form(1)):
        resp.media = {"name": name, "count": count}

    @api.post("/upload", operation_id="upload_file")
    async def upload(req, resp, *, label: str = Form(...), data=File(...)):
        content = await data.read()
        resp.media = {"label": label, "size": len(content)}

    return api


def test_python_client_sends_urlencoded_form_body(tmp_path):
    api = _form_api()
    path = tmp_path / "client.py"
    api.generate_client(path, class_name="Client")
    client = _load_module(path).Client(session=api.requests)

    # Sent as a form, the server's Form() markers resolve (JSON would 422).
    assert client.submit_form(body={"name": "tea", "count": 3}) == {
        "name": "tea",
        "count": 3,
    }


def test_python_client_sends_multipart_body(tmp_path):
    api = _form_api()
    path = tmp_path / "client.py"
    api.generate_client(path, class_name="Client")
    client = _load_module(path).Client(session=api.requests)

    result = client.upload_file(
        body={"label": "pic", "data": ("pic.bin", b"\x00\x01\x02")}
    )
    assert result == {"label": "pic", "size": 3}


def test_python_multipart_encoder_for_urllib_transport(tmp_path):
    api = _form_api()
    path = tmp_path / "client.py"
    api.generate_client(path, class_name="Client")
    client = _load_module(path).Client(base_url="http://example.invalid")

    data, content_type = client._encode_multipart(
        {"label": "pic", "data": ("pic.bin", b"\x00\x01", "image/x-raw")}
    )
    assert content_type.startswith("multipart/form-data; boundary=")
    boundary = content_type.split("boundary=", 1)[1]
    assert data.startswith(f"--{boundary}\r\n".encode())
    assert data.endswith(f"--{boundary}--\r\n".encode())
    assert b'Content-Disposition: form-data; name="label"' in data
    assert b'name="data"; filename="pic.bin"' in data
    assert b"Content-Type: image/x-raw" in data
    assert b"\x00\x01" in data


def test_other_languages_send_form_and_multipart_bodies():
    api = _form_api()
    js = generate_client(api, language="javascript")
    assert "formBody: body" in js
    assert "multipartBody: body" in js
    assert "new FormData()" in js
    assert "new URLSearchParams()" in js
    ts = generate_client(api, language="typescript")
    assert "multipartBody: body" in ts
    rb = generate_client(api, language="ruby")
    assert "form_body: body" in rb
    assert "multipart_body: body" in rb
    assert "req.set_form(form, 'multipart/form-data')" in rb
    assert "req.set_form_data" in rb
    php = generate_client(api, language="php")
    assert "formBody: $body" in php
    assert "multipartBody: $body" in php
    assert "http_build_query" in php
    assert "multipart/form-data; boundary=" in php


# ---------------------------------------------------------------------------
# Item 68: parameter-name collisions generate correct code.
# ---------------------------------------------------------------------------


def _collision_spec():
    return {
        "openapi": "3.1.0",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/c/{user_id}": {
                "get": {
                    "operationId": "collide",
                    "parameters": [
                        {
                            "name": "user_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "user-id",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "body",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": {"type": "object"}}
                        },
                    },
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }


def test_python_client_handles_parameter_name_collisions():
    source = generate_client(_collision_spec())
    compile(source, "<generated>", "exec")  # duplicate args would SyntaxError
    # The deduped path parameter is the one interpolated into the URL.
    assert (
        "def collide(self, user_id: str, user_id_: str, body_: str, "
        "body: dict[str, Any])" in source
    )
    assert ".replace('{user_id}', _quote(user_id))" in source
    # The query dict uses the deduped names, keyed by the raw wire names.
    assert "query={'user-id': user_id_, 'body': body_}" in source


def test_js_client_handles_parameter_name_collisions():
    source = generate_client(_collision_spec(), language="javascript")
    assert "collide(userId, userId_, body_, body)" in source
    assert ".replace('{user_id}', quote(userId))" in source
    assert "query: {'user-id': userId_, 'body': body_}" in source


def test_ruby_and_php_clients_dedupe_colliding_parameters():
    rb = generate_client(_collision_spec(), language="ruby")
    assert "def collide(user_id, user_id_, body_, body)" in rb
    assert ".gsub('{user_id}', quote(user_id))" in rb
    php = generate_client(_collision_spec(), language="php")
    assert (
        "public function collide($user_id, $user_id_, $body_, $body): mixed" in php
    )
    assert "$this->quote($user_id), $path);" in php


def test_path_parameter_named_path_does_not_shadow_local():
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/files/{path}": {
                "get": {
                    "operationId": "get_file",
                    "parameters": [
                        {
                            "name": "path",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    js = generate_client(spec, language="javascript")
    # `const path = ...quote(path)` would be a TDZ/redeclaration error.
    assert "get_file(path_)" in js
    assert ".replace('{path}', quote(path_))" in js
    py = generate_client(spec)
    compile(py, "<generated>", "exec")
    assert "def get_file(self, path_: str)" in py
    assert ".replace('{path}', _quote(path_))" in py


# ---------------------------------------------------------------------------
# Finding A: a user route at the implied schema path must not crash the app.
# ---------------------------------------------------------------------------


def test_user_route_at_implied_schema_path_wins(caplog):
    # On 8.0.0, docs_route alone registered no schema route, so apps could
    # (and did) serve their own /schema.yml; the implied route must yield.
    api = responder.API(
        docs_route="/docs/", allowed_hosts=[";"], session_https_only=False
    )

    with caplog.at_level("WARNING", logger="responder.openapi"):

        @api.route("/schema.yml")
        def my_schema(req, resp):
            resp.text = "custom schema"

    assert any("yields" in record.getMessage() for record in caplog.records)
    response = api.requests.get("http://;/schema.yml")
    assert response.status_code == 200
    assert response.text == "custom schema"
    # The docs page still renders (and points at the user's schema route).
    assert api.requests.get("http://;/docs/").status_code == 200


def test_explicit_openapi_schema_route_still_conflicts():
    # With openapi= given explicitly, the 8.0.0 duplicate-route error stays.
    api = _api()
    with pytest.raises(ValueError, match="already exists"):

        @api.route("/schema.yml")
        def my_schema(req, resp):
            pass  # pragma: no cover


# ---------------------------------------------------------------------------
# Finding B: same-named hoisted $defs must not merge into one component.
# ---------------------------------------------------------------------------


def test_same_named_enums_get_distinct_components():
    other_color = enum.Enum("Color", {"green": "green", "yellow": "yellow"})
    api = _api()

    @api.get("/a")
    def a(req, resp, *, colors: list[Color] = Query([])):
        resp.media = {}

    @api.get("/b")
    def b(req, resp, *, colors: list[other_color] = Query([])):
        resp.media = {}

    @api.get("/c")
    def c(req, resp, *, colors: list[Color] = Query([])):
        resp.media = {}

    spec = _spec_of(api)
    schemas = spec["components"]["schemas"]
    assert schemas["Color"]["enum"] == ["red", "blue"]
    assert schemas["Color_2"]["enum"] == ["green", "yellow"]

    def item_ref(path):
        parameter = spec["paths"][path]["get"]["parameters"][0]
        return parameter["schema"]["items"]["$ref"]

    assert item_ref("/a") == "#/components/schemas/Color"
    assert item_ref("/b") == "#/components/schemas/Color_2"
    # An identical body shares the existing component.
    assert item_ref("/c") == "#/components/schemas/Color"
    validate(spec)


# ---------------------------------------------------------------------------
# Finding C: a parameter named `quote` must not shadow the JS/TS URL helper.
# ---------------------------------------------------------------------------


def _quote_param_spec():
    return {
        "openapi": "3.1.0",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/q/{quote}": {
                "get": {
                    "operationId": "get_quote",
                    "parameters": [
                        {
                            "name": "quote",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }


def test_js_parameter_named_quote_does_not_shadow_helper():
    js = generate_client(_quote_param_spec(), language="javascript")
    # `quote(quote)` would call the string parameter -> TypeError.
    assert "get_quote(quote_)" in js
    assert ".replace('{quote}', quote(quote_))" in js
    ts = generate_client(_quote_param_spec(), language="typescript")
    assert "get_quote(quote_: string)" in ts
    assert ".replace('{quote}', quote(quote_))" in ts


# ---------------------------------------------------------------------------
# Finding D: Ruby multipart must send file parts with filenames.
# ---------------------------------------------------------------------------


def test_ruby_multipart_sends_file_parts_with_filenames():
    api = _form_api()
    rb = generate_client(api, language="ruby")
    # [filename, content(, content_type)] pairs mirror the Python client.
    assert "opts = { filename: value[0].to_s }" in rb
    assert "opts[:content_type] = value[2].to_s if value.length > 2" in rb
    # IO-like values (StringIO) default their filename to the field name.
    assert "elsif value.respond_to?(:read)" in rb
    assert "filename = key.to_s" in rb
    assert "req.set_form(form, 'multipart/form-data')" in rb


# ---------------------------------------------------------------------------
# Finding E: PHP must only treat strict file specs as file parts.
# ---------------------------------------------------------------------------


def test_php_multipart_distinguishes_file_specs_from_lists():
    api = _form_api()
    php = generate_client(api, language="php")
    # A file spec must carry 'content' and only the documented keys.
    assert "array_key_exists('content', $value)" in php
    assert "['filename', 'content', 'content_type']" in php
    # Plain arrays become repeated form fields, one part per item.
    assert "$items = $value;" in php
    assert "foreach ($items as $data)" in php
