"""response_model documents raw bodies instead of rejecting them.

A handler that assigns ``resp.content``, ``resp.text``, a file, or a stream
keeps that body. ``resp.media`` is still validated. The model stays on the
OpenAPI operation and the MCP tool ``outputSchema``.
"""

from __future__ import annotations

import json

import yaml
from pydantic import BaseModel

import responder


class Item(BaseModel):
    id: int
    name: str


class GetResponse(BaseModel):
    url: str
    args: dict


def _api(**kwargs) -> responder.API:
    options = {
        "title": "Channels",
        "version": "1",
        "openapi": "3.1.0",
        "allowed_hosts": [";"],
        "sessions": False,
    }
    options.update(kwargs)
    return responder.API(**options)


def _rpc(api, method, params=None, *, rpc_id=1):
    payload = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        payload["params"] = params
    response = api.requests.post("/mcp", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == rpc_id
    return body


def _tools(api):
    listed = _rpc(api, "tools/list")
    return {tool["name"]: tool for tool in listed["result"]["tools"]}


def test_response_model_passes_content_text_and_files_through(tmp_path):
    api = _api()
    raw = b'{\n  "args": {},\n  "url": "http://test/get"\n}\n'
    payload = tmp_path / "pic.bin"
    payload.write_bytes(b"\x89PNG\r\nraw")

    @api.get("/get", response_model=GetResponse)
    def view_get(req, resp):
        resp.content = raw
        resp.mimetype = "application/json"

    @api.get("/note", response_model=Item)
    def note(req, resp):
        resp.text = "plain note"

    @api.get("/page", response_model=Item)
    def page(req, resp):
        resp.html = "<p>hi</p>"

    @api.get("/pic", response_model=Item)
    def pic(req, resp):
        resp.file(payload, content_type="application/octet-stream")

    # Bytes that do not satisfy the model must still be the handler's bytes.
    @api.get("/odd", response_model=Item)
    def odd(req, resp):
        resp.content = b"not-json"
        resp.mimetype = "text/plain"

    got = api.requests.get("/get")
    assert got.status_code == 200
    assert got.content == raw
    assert got.headers["content-type"].startswith("application/json")

    noted = api.requests.get("/note")
    assert noted.status_code == 200
    assert noted.text == "plain note"
    assert noted.headers["content-type"].startswith("text/plain")

    html = api.requests.get("/page")
    assert html.status_code == 200
    assert html.text == "<p>hi</p>"
    assert html.headers["content-type"].startswith("text/html")

    filed = api.requests.get("/pic")
    assert filed.status_code == 200
    assert filed.content == b"\x89PNG\r\nraw"

    odd_response = api.requests.get("/odd")
    assert odd_response.status_code == 200
    assert odd_response.content == b"not-json"

    spec = yaml.safe_load(api.requests.get("/schema.yml").content)
    schema = spec["paths"]["/get"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert schema["$ref"].endswith("/GetResponse")
    assert "GetResponse" in spec["components"]["schemas"]


def test_response_model_passes_a_stream_through():
    api = _api()

    @api.get("/stream/{n:int}", response_model=Item)
    async def stream_n(req, resp, *, n: int):
        resp.mimetype = "application/json"

        @resp.stream
        async def body():
            for i in range(n):
                yield f'{{"id": {i}}}\n'.encode()

    response = api.requests.get("/stream/2")
    assert response.status_code == 200
    assert response.content == b'{"id": 0}\n{"id": 1}\n'
    assert response.headers["content-type"].startswith("application/json")


def test_response_model_still_validates_media():
    api = _api()

    @api.get("/ok", response_model=Item)
    def ok(req, resp):
        resp.media = {"id": "1", "name": "tea", "extra": True}

    @api.get("/bad", response_model=Item)
    def bad(req, resp):
        resp.media = {"name": "tea"}

    good = api.requests.get("/ok")
    assert good.status_code == 200
    assert good.json() == {"id": 1, "name": "tea"}

    invalid = api.requests.get("/bad")
    assert invalid.status_code == 500
    assert invalid.headers["content-type"].startswith("application/problem+json")


def test_mcp_tools_keep_output_schema_for_content_and_streams():
    api = _api(mcp=True)

    @api.get("/get", response_model=GetResponse)
    def view_get(req, resp):
        """Echo the request URL."""
        resp.content = b'{\n  "args": {},\n  "url": "http://test/get"\n}\n'
        resp.mimetype = "application/json"

    @api.get("/stream", response_model=Item)
    async def stream_items(req, resp):
        """Stream item lines."""
        resp.mimetype = "application/json"

        @resp.stream
        async def body():
            yield b'{"id": 1, "name": "tea"}\n'

    @api.get("/hidden", response_model=False)
    def hidden(req, resp) -> Item:
        """Opted out of the model entirely."""
        resp.text = "secret"

    tools = _tools(api)
    assert tools["view_get"]["outputSchema"]["type"] == "object"
    assert "url" in tools["view_get"]["outputSchema"]["properties"]
    assert "args" in tools["view_get"]["outputSchema"]["properties"]
    assert tools["stream_items"]["outputSchema"]["type"] == "object"
    assert set(tools["stream_items"]["outputSchema"]["properties"]) >= {"id", "name"}
    assert "outputSchema" not in tools["hidden"]

    called = _rpc(
        api,
        "tools/call",
        {"name": "view_get", "arguments": {}},
        rpc_id=2,
    )["result"]
    assert called["isError"] is False
    assert json.loads(called["content"][0]["text"])["url"] == "http://test/get"
    assert called["structuredContent"]["args"] == {}


def test_unset_body_is_empty_and_explicit_null_stays_json():
    api = _api(openapi=None)

    @api.get("/away")
    def away(req, resp):
        resp.status_code = 302
        resp.headers["Location"] = "/get"

    @api.get("/null")
    def explicit_null(req, resp):
        resp.media = None

    redirected = api.requests.get("/away", follow_redirects=False)
    assert redirected.status_code == 302
    assert redirected.headers["location"] == "/get"
    assert redirected.content == b""
    assert "content-type" not in redirected.headers

    null = api.requests.get("/null")
    assert null.status_code == 200
    assert null.content == b"null"
    assert null.headers["content-type"].startswith("application/json")
