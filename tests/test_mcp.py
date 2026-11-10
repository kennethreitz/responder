"""Model Context Protocol mount: discovery and in-process tool calls."""

import json

import pytest
import yaml
from pydantic import BaseModel

import responder
from responder import Cookie, File, Form, Header, Query, Router


class ItemIn(BaseModel):
    name: str
    qty: int = 1


class ItemOut(BaseModel):
    id: int
    name: str
    qty: int


def _rpc(api, method, params=None, *, rpc_id=1, headers=None, path="/mcp"):
    payload = {"jsonrpc": "2.0", "method": method}
    if rpc_id is not None:
        payload["id"] = rpc_id
    if params is not None:
        payload["params"] = params
    return api.requests.post(path, json=payload, headers=headers)


def _tools(api, path="/mcp", headers=None):
    response = _rpc(api, "tools/list", path=path, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == 1
    return {tool["name"]: tool for tool in body["result"]["tools"]}


def _call(api, name, arguments=None, *, headers=None, path="/mcp"):
    response = _rpc(
        api,
        "tools/call",
        {"name": name, "arguments": arguments or {}},
        rpc_id=2,
        headers=headers,
        path=path,
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


def test_mcp_is_off_until_mounted(api):
    @api.get("/hello")
    def hello(req, resp):
        resp.text = "hi"

    assert api.requests.post("/mcp").status_code == 404
    assert api.requests.get("/hello").text == "hi"


def test_tools_list_and_call_round_trip(api):
    """End to end: tools/list describes the route, tools/call runs it."""
    api.mcp()

    @api.get("/items/{item_id:int}", operation_id="get_item")
    def get_item(
        req, resp, *, item_id: int, limit: int = Query(10), q: str = Query(...)
    ) -> ItemOut:
        """Fetch one item."""
        return ItemOut(id=item_id, name=q, qty=limit)

    @api.post("/items", summary="Create an item")
    def create_item(req, resp, *, item: ItemIn, token: str = Header(..., alias="X-Token")):
        """Create an item from a JSON body."""
        resp.status_code = 201
        resp.media = {"id": 7, "name": item.name, "qty": item.qty, "token": token}

    @api.get("/admin", mcp=False)
    def admin(req, resp):
        """Not a tool."""
        resp.text = "secret"

    @api.get("/later")
    def later(req, resp):
        """Registered after the mount."""
        resp.text = "later"

    tools = _tools(api)
    assert "admin" not in tools
    assert "later" in tools

    fetched = tools["get_item"]
    assert fetched["description"] == "Fetch one item."
    assert fetched["annotations"]["readOnlyHint"] is True
    schema = fetched["inputSchema"]
    assert schema["properties"]["item_id"]["type"] == "integer"
    assert schema["properties"]["limit"]["type"] == "integer"
    assert "q" in schema["properties"]
    assert set(schema["required"]) == {"item_id", "q"}
    assert fetched["outputSchema"]["type"] == "object"
    assert "id" in fetched["outputSchema"]["properties"]

    created = tools["create_item"]
    assert created["title"] == "Create an item"
    assert "Create an item from a JSON body." in created["description"]
    assert created["inputSchema"]["properties"]["name"]["type"] == "string"
    assert "qty" in created["inputSchema"]["properties"]
    assert "X-Token" in created["inputSchema"]["properties"]
    assert "name" in created["inputSchema"]["required"]
    assert "X-Token" in created["inputSchema"]["required"]
    assert "qty" not in created["inputSchema"]["required"]

    result = _call(api, "get_item", {"item_id": 3, "q": "Ada"})
    assert result["isError"] is False
    assert result["structuredContent"] == {"id": 3, "name": "Ada", "qty": 10}

    created_result = _call(
        api, "create_item", {"name": "Pen", "X-Token": "secret"}
    )
    assert created_result["isError"] is False
    assert created_result["structuredContent"] == {
        "id": 7,
        "name": "Pen",
        "qty": 1,
        "token": "secret",
    }

    invalid = _call(api, "create_item", {"X-Token": "secret"})
    assert invalid["isError"] is True
    assert "HTTP 422" in invalid["content"][0]["text"]

    missing = _rpc(
        api, "tools/call", {"name": "get_item", "arguments": {}}, rpc_id=9
    )
    assert missing.json()["error"]["code"] == -32602
    assert "item_id" in missing.json()["error"]["message"]

    # The route still answers ordinary HTTP after the mount.
    direct = api.requests.get("/items/4", params={"q": "Grace"})
    assert direct.status_code == 200
    assert direct.json()["name"] == "Grace"


def test_initialize_ping_and_notifications(api):
    api = responder.API(
        title="Shop",
        version="1.2",
        openapi="3.1.0",
        mcp=True,
        allowed_hosts=[";"],
        session_https_only=False,
    )

    initialized = _rpc(
        api,
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    )
    result = initialized.json()["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["serverInfo"] == {"name": "Shop", "version": "1.2"}
    assert result["capabilities"]["tools"]["listChanged"] is False
    assert "route" in result["instructions"]

    negotiated = _rpc(
        api,
        "initialize",
        {"protocolVersion": "1999-01-01", "capabilities": {}},
    )
    assert negotiated.json()["result"]["protocolVersion"] == "2025-11-25"

    ping = _rpc(api, "ping", rpc_id="abc")
    assert ping.json() == {"jsonrpc": "2.0", "id": "abc", "result": {}}

    note = api.requests.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert note.status_code == 202
    assert note.content == b""

    missing = _rpc(api, "resources/list")
    assert missing.json()["error"]["code"] == -32601


def test_streamable_http_framing(api):
    api.mcp(path="/agents")

    @api.get("/ping")
    def ping(req, resp):
        resp.text = "pong"

    assert api.requests.post("/mcp").status_code == 404
    listed = _tools(api, path="/agents")
    assert _call(api, "ping", path="/agents")["content"][0]["text"] == "pong"
    assert "ping" in listed

    get = api.requests.get("/agents")
    assert get.status_code == 405
    assert get.headers["allow"] == "POST"

    deleted = api.requests.delete("/agents")
    assert deleted.status_code == 405

    rejected = api.requests.post(
        "/agents",
        headers={"Origin": "http://evil.example", "Content-Type": "application/json"},
        content=b"{}",
    )
    assert rejected.status_code == 403

    unacceptable = api.requests.post(
        "/agents",
        headers={"Accept": "text/html", "Content-Type": "application/json"},
        content=b"{}",
    )
    assert unacceptable.status_code == 406

    stream = api.requests.post(
        "/agents",
        headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
        content=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        ).encode(),
    )
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "pong" not in stream.text
    assert '"result"' in stream.text

    parse = api.requests.post(
        "/agents",
        headers={"Content-Type": "application/json"},
        content=b"not-json",
    )
    assert parse.status_code == 200
    assert parse.json()["error"]["code"] == -32700

    batch = api.requests.post("/agents", json=[])
    assert batch.json()["error"]["code"] == -32600

    unknown = _rpc(api, "tools/call", {"name": "missing", "arguments": {}}, path="/agents")
    assert unknown.json()["error"]["code"] == -32602


def test_explicit_include_and_per_route_snapshot(api):
    api.mcp(include="explicit")

    @api.get("/yes", mcp=True)
    def yes(req, resp):
        resp.text = "yes"

    @api.get("/no")
    def no(req, resp):
        resp.text = "no"

    def shared(req, resp):
        resp.text = "shared"

    api.route("/private", methods=["GET"], mcp=False)(shared)
    api.route("/public", methods=["GET"])(shared)

    names = set(_tools(api))
    assert names == {"yes"}
    assert _call(api, "yes")["content"][0]["text"] == "yes"

    with pytest.raises(RuntimeError):
        api.mcp()
    with pytest.raises(ValueError):
        responder.API(allowed_hosts=[";"]).mcp("agents")
    with pytest.raises(TypeError):

        @api.get("/bad", mcp="no")
        def bad(req, resp):
            resp.text = "no"


def test_dependencies_auth_and_forms_still_run(api):
    seen = {}

    @api.dependency()
    def resource():
        seen["open"] = True
        yield "ok"
        seen["closed"] = True

    @api.before_request
    def gate(req, resp):
        if req.url.path == "/mcp":
            return
        if req.headers.get("X-Custom-Auth") != "yes":
            resp.status_code = 401
            resp.media = {"error": "unauthorized"}

    @api.get("/res")
    def view(req, resp, *, resource):
        resp.text = resource

    @api.post("/form")
    def form(req, resp, *, q: str = Form(...)):
        resp.media = {"q": q}

    @api.get("/cookie")
    def cookie(req, resp, *, session_id: str = Cookie(alias="sid")):
        resp.media = {"sid": session_id}

    api.mcp()
    tools = _tools(api)
    assert "resource" not in tools["view"]["inputSchema"]["properties"]

    blocked = _call(api, "view")
    assert blocked["isError"] is True
    assert "HTTP 401" in blocked["content"][0]["text"]

    allowed = _call(api, "view", headers={"X-Custom-Auth": "yes"})
    assert allowed["isError"] is False
    assert allowed["content"][0]["text"] == "ok"
    assert seen == {"open": True, "closed": True}

    formed = _call(api, "form", {"q": "hello"}, headers={"X-Custom-Auth": "yes"})
    assert formed["structuredContent"] == {"q": "hello"}

    cooked = _call(
        api, "cookie", {"sid": "abc"}, headers={"X-Custom-Auth": "yes"}
    )
    assert cooked["structuredContent"] == {"sid": "abc"}


def test_csrf_exempts_the_endpoint_and_still_guards_tool_calls():
    api = responder.API(
        csrf=True,
        mcp=True,
        allowed_hosts=[";"],
        session_https_only=False,
    )

    @api.get("/token")
    def token(req, resp):
        resp.media = {"token": req.csrf_token}

    @api.post("/items")
    def create(req, resp):
        resp.media = {"ok": True}

    client = api.requests
    issued = client.get("/token")
    assert issued.status_code == 200
    token_value = issued.json()["token"]

    listed = _rpc(api, "tools/list")
    assert listed.status_code == 200
    assert "create" in {tool["name"] for tool in listed.json()["result"]["tools"]}

    call = {
        "name": "create",
        "arguments": {},
    }
    blocked = _rpc(api, "tools/call", call, rpc_id=2)
    assert blocked.json()["result"]["isError"] is True
    assert "HTTP 403" in blocked.json()["result"]["content"][0]["text"]

    allowed = _rpc(
        api,
        "tools/call",
        call,
        rpc_id=3,
        headers={"X-CSRF-Token": token_value},
    )
    assert allowed.json()["result"]["isError"] is False
    assert allowed.json()["result"]["structuredContent"] == {"ok": True}


def test_router_class_views_uploads_and_openapi(api):
    router = Router(prefix="/v1")

    @router.get("/pings")
    def pings(req, resp):
        resp.text = "pong"

    api.include_router(router)

    @api.route("/widgets/{widget_id:int}")
    class Widget:
        def on_get(self, req, resp, *, widget_id: int):
            """Fetch a widget."""
            resp.media = {"id": widget_id}

        def on_delete(self, req, resp, *, widget_id: int):
            resp.status_code = 204

    @api.post("/upload")
    async def upload(req, resp, *, upload: bytes = File(...)):
        resp.text = "no"

    @api.sse("/events", event_model=ItemOut)
    async def events(req, resp):
        yield ItemOut(id=1, name="A", qty=1)

    @api.get("/a")
    @api.post("/b")
    def hello(req, resp):
        resp.text = req.method

    api.mcp()
    tools = _tools(api)
    assert "pings" in tools
    assert _call(api, "pings")["content"][0]["text"] == "pong"
    assert "Widget_get" in tools
    assert tools["Widget_get"]["description"] == "Fetch a widget."
    assert _call(api, "Widget_get", {"widget_id": 5})["structuredContent"] == {"id": 5}
    assert "Widget_delete" in tools
    deleted = _call(api, "Widget_delete", {"widget_id": 5})
    assert deleted["isError"] is False
    assert "upload" not in tools
    assert "events" not in tools

    getters = [name for name, tool in tools.items() if tool["annotations"]["readOnlyHint"]]
    assert "hello_get" in getters or "hello" in getters
    hello_get = "hello_get" if "hello_get" in tools else "hello"
    assert _call(api, hello_get)["content"][0]["text"] == "GET"

    documented = responder.API(
        openapi="3.1.0",
        docs_route="/docs",
        mcp=True,
        allowed_hosts=[";"],
        session_https_only=False,
    )

    @documented.get("/hello")
    def hello_doc(req, resp):
        resp.text = "hi"

    spec = yaml.safe_load(documented.requests.get("/schema.yml").text)
    assert "/hello" in spec["paths"]
    assert "/mcp" not in spec["paths"]
    assert "/docs" not in spec["paths"]


class Note(BaseModel):
    item_id: int
    name: str


class Find(BaseModel):
    q: str


def test_schema_edges_and_protocol_errors(api):
    @api.get("/mcp")
    def taken(req, resp):
        resp.text = "taken"

    with pytest.raises(ValueError, match="already registered"):
        api.mcp()

    fresh = responder.API(allowed_hosts=[";"], session_https_only=False)

    @fresh.post("/rows/{item_id}")
    def update(req, resp, *, item_id: int, note: Note):
        resp.media = {"path": item_id, "body": note.item_id, "name": note.name}

    @fresh.post("/find")
    def find(req, resp, *, search: Find = Form(...)):
        resp.media = {"q": search.q}

    @fresh.get("/tags")
    def tags(req, resp, *, tag: list[str] = Query([]), flag: bool = Query(False)):
        resp.media = {"tag": tag, "flag": flag}

    @fresh.get("/nums")
    def nums(req, resp):
        resp.media = [1, 2]

    @fresh.get("/one", operation_id="echo")
    def first(req, resp):
        resp.text = "one"

    @fresh.get("/two", operation_id="echo_get")
    def second(req, resp):
        resp.text = "two"

    @fresh.get("/three", operation_id="echo")
    def third(req, resp):
        resp.text = "three"

    @fresh.get("/jar")
    def jar(req, resp, *, sid: str = Cookie(alias="sid")):
        resp.media = {"sid": sid, "other": req.cookies.get("other")}

    with pytest.raises(ValueError):
        fresh.mcp(include="sometimes")
    with pytest.raises(ValueError):
        fresh.mcp("/items/{item_id}")
    with pytest.raises(TypeError):
        responder.API(mcp=1, allowed_hosts=[";"])

    fresh.mcp(path="/agents/", name="Tools", instructions="Be brief.")
    init = _rpc(
        fresh,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}},
        path="/agents",
    )
    assert init.json()["result"]["serverInfo"]["name"] == "Tools"
    assert init.json()["result"]["instructions"] == "Be brief."

    tools = _tools(fresh, path="/agents")
    assert "item_id" in tools["update"]["inputSchema"]["properties"]
    assert "note" in tools["update"]["inputSchema"]["properties"]
    assert "properties" in tools["update"]["inputSchema"]["properties"]["note"]
    updated = _call(
        fresh,
        "update",
        {"item_id": 4, "note": {"item_id": 9, "name": "Pen"}},
        path="/agents",
    )
    assert updated["structuredContent"] == {"path": 4, "body": 9, "name": "Pen"}

    assert _call(fresh, "find", {"q": "hi"}, path="/agents")["structuredContent"] == {
        "q": "hi"
    }
    tagged = _call(
        fresh, "tags", {"tag": ["a", "b"], "flag": False}, path="/agents"
    )
    assert tagged["structuredContent"] == {"tag": ["a", "b"], "flag": False}

    numbers = _call(fresh, "nums", path="/agents")
    assert numbers["isError"] is False
    assert "structuredContent" not in numbers
    assert "1" in numbers["content"][0]["text"]

    assert "echo_get_2" in tools
    assert _call(fresh, "echo_get_2", path="/agents")["content"][0]["text"] == "three"

    jarred = _call(
        fresh,
        "jar",
        {"sid": "abc"},
        headers={"Cookie": "other=1"},
        path="/agents",
    )
    assert jarred["structuredContent"] == {"sid": "abc", "other": "1"}

    stale = fresh.requests.post(
        "/agents",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"MCP-Protocol-Version": "1999-01-01"},
    )
    assert stale.status_code == 400

    old = fresh.requests.post(
        "/agents",
        json={"jsonrpc": "1.0", "id": 1, "method": "ping"},
    )
    assert old.json()["error"]["code"] == -32600

    listed = fresh.requests.post(
        "/agents",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []},
    )
    assert listed.json()["error"]["code"] == -32602

    bad_args = _rpc(
        fresh,
        "tools/call",
        {"name": "nums", "arguments": []},
        path="/agents",
    )
    assert bad_args.json()["error"]["code"] == -32602


def test_tool_description_omits_openapi_docstring_yaml():
    """Prose above ``---`` is the tool text. OpenAPI still reads the YAML."""
    api = responder.API(
        openapi="3.1.0",
        mcp=True,
        allowed_hosts=[";"],
        session_https_only=False,
    )

    @api.get("/decode/{value}")
    def decode_base64(req, resp, *, value: str):
        """Decodes base64url-encoded string.
        ---
        get:
            tags: [Dynamic data]
            summary: Decodes a base64url-encoded string.
        """
        resp.text = value

    @api.get("/yaml-only")
    def yaml_only(req, resp):
        """
        ---
        get:
            summary: Just the summary
        """
        resp.text = "ok"

    @api.post("/yaml-empty")
    def yaml_empty(req, resp):
        """
        ---
        post:
            tags: [Dynamic data]
        """
        resp.text = "ok"

    @api.get("/kept", description="Keep the ---\nwhole thing")
    def kept(req, resp):
        """Ignored prose.
        ---
        get:
            summary: Not this
        """
        resp.text = "ok"

    tools = _tools(api)
    assert tools["decode_base64"]["description"] == (
        "Decodes base64url-encoded string."
    )
    assert "---" not in tools["decode_base64"]["description"]
    assert "tags:" not in tools["decode_base64"]["description"]
    assert tools["yaml_only"]["description"] == "Just the summary"
    assert tools["yaml_empty"]["description"] == ""
    assert tools["kept"]["description"] == "Keep the ---\nwhole thing"

    spec = yaml.safe_load(api.requests.get("/schema.yml").content)
    operation = spec["paths"]["/decode/{value}"]["get"]
    assert operation["summary"] == "Decodes a base64url-encoded string."
    assert operation["tags"] == ["Dynamic data"]

    # A longer dash line is not the OpenAPI fence. Apispec's loader treats
    # any line that starts with ``---`` as YAML, so this route stays off the
    # schema app above.
    plain = responder.API(
        openapi=None, mcp=True, allowed_hosts=[";"], sessions=False
    )

    @plain.get("/dashes")
    def dashes(req, resp):
        """Not a fence
        ----
        still prose
        """
        resp.text = "ok"

    assert _tools(plain)["dashes"]["description"] == "Not a fence\n----\nstill prose"
