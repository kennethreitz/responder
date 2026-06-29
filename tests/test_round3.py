"""Tests for WebSocket dependency injection, OpenAPI 3.1/path-parameter
generation, and content-negotiated error responses."""

import pytest
import yaml
from starlette.testclient import TestClient as StarletteTestClient

import responder

# --- WebSocket dependency injection & path params ---


def test_websocket_path_param_injection(api):
    @api.route("/ws/{room}", websocket=True)
    async def chat(ws, *, room):
        await ws.accept()
        await ws.send_text(f"joined {room}")
        await ws.close()

    client = StarletteTestClient(api)
    with client.websocket_connect("ws://;/ws/lobby") as ws:
        assert ws.receive_text() == "joined lobby"


def test_websocket_plain_path_annotation_coerces(api):
    @api.route("/ws/{room_id}", websocket=True)
    async def chat(ws, *, room_id: int):
        await ws.accept()
        await ws.send_json({"room_id": room_id, "type": type(room_id).__name__})
        await ws.close()

    client = StarletteTestClient(api)
    with client.websocket_connect("ws://;/ws/42") as ws:
        assert ws.receive_json() == {"room_id": 42, "type": "int"}


def test_websocket_invalid_plain_path_annotation_closes(api):
    from starlette.websockets import WebSocketDisconnect

    @api.route("/ws/{room_id}", websocket=True)
    async def chat(ws, *, room_id: int):
        await ws.accept()
        await ws.send_text(str(room_id))

    client = StarletteTestClient(api)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("ws://;/ws/nope"):
            pass
    assert excinfo.value.code == 1008


def test_websocket_plain_handler_unaffected(api):
    """Handlers that only take (ws) keep working on parameterized routes."""

    @api.route("/ws/{room}", websocket=True)
    async def chat(ws):
        await ws.accept()
        await ws.send_text(ws.path_params["room"])
        await ws.close()

    client = StarletteTestClient(api)
    with client.websocket_connect("ws://;/ws/den") as ws:
        assert ws.receive_text() == "den"


def test_websocket_dependency_injection(api):
    @api.dependency()
    def motd():
        return "welcome!"

    @api.route("/ws", websocket=True)
    async def greet(ws, *, motd):
        await ws.accept()
        await ws.send_text(motd)
        await ws.close()

    client = StarletteTestClient(api)
    with client.websocket_connect("ws://;/ws") as ws:
        assert ws.receive_text() == "welcome!"


def test_websocket_dependency_receives_websocket(api):
    @api.dependency()
    def origin(ws):
        return ws.headers.get("X-Origin", "unknown")

    @api.route("/ws", websocket=True)
    async def echo_origin(ws, *, origin):
        await ws.accept()
        await ws.send_text(origin)
        await ws.close()

    client = StarletteTestClient(api)
    with client.websocket_connect("ws://;/ws", headers={"X-Origin": "test"}) as ws:
        assert ws.receive_text() == "test"


def test_websocket_generator_dependency_teardown(api):
    events = []

    @api.dependency()
    def session():
        events.append("open")
        yield "sess"
        events.append("close")

    @api.route("/ws", websocket=True)
    async def handler(ws, *, session):
        await ws.accept()
        await ws.send_text(session)
        await ws.close()

    client = StarletteTestClient(api)
    with client.websocket_connect("ws://;/ws") as ws:
        assert ws.receive_text() == "sess"
    assert events == ["open", "close"]


def test_websocket_app_scoped_dependency(api):
    calls = []

    @api.dependency(scope="app")
    def hub():
        calls.append(1)
        return {"clients": []}

    @api.route("/ws", websocket=True)
    async def handler(ws, *, hub):
        await ws.accept()
        await ws.send_text(str(len(calls)))
        await ws.close()

    client = StarletteTestClient(api)
    for _ in range(2):
        with client.websocket_connect("ws://;/ws") as ws:
            assert ws.receive_text() == "1"


# --- OpenAPI improvements ---


def test_openapi_31_support(needs_openapi):
    api = responder.API(
        title="Service", version="1.0", openapi="3.1.0", allowed_hosts=[";"]
    )

    @api.route("/items/{id:int}")
    def get_item(req, resp, *, id):
        """An item.
        ---
        get:
            description: Get an item
            responses:
                200:
                    description: The item
        """
        resp.media = {"id": id}

    dump = yaml.safe_load(api.requests.get("/schema.yml").content)
    assert dump["openapi"] == "3.1.0"
    assert "/items/{id}" in dump["paths"]


def test_openapi_path_templates_strip_convertors(needs_openapi):
    api = responder.API(
        title="Service", version="1.0", openapi="3.0.2", allowed_hosts=[";"]
    )

    @api.route("/users/{user_id:int}")
    def get_user(req, resp, *, user_id):
        """A user.
        ---
        get:
            description: Get a user
            responses:
                200:
                    description: The user
        """
        resp.media = {}

    dump = yaml.safe_load(api.requests.get("/schema.yml").content)
    # The raw `{user_id:int}` pattern must not leak into the spec.
    assert "/users/{user_id}" in dump["paths"]
    assert "/users/{user_id:int}" not in dump["paths"]


def test_openapi_path_parameters_documented(needs_openapi):
    from pydantic import BaseModel

    class Out(BaseModel):
        id: int

    api = responder.API(
        title="Service", version="1.0", openapi="3.0.2", allowed_hosts=[";"]
    )

    @api.route("/things/{thing_id:int}", methods=["GET"], response_model=Out)
    def get_thing(req, resp, *, thing_id):
        resp.media = {"id": thing_id}

    dump = yaml.safe_load(api.requests.get("/schema.yml").content)
    params = dump["paths"]["/things/{thing_id}"]["get"]["parameters"]
    assert params == [
        {
            "name": "thing_id",
            "in": "path",
            "required": True,
            "schema": {"type": "integer"},
        }
    ]


def test_openapi_class_based_view_method_params_documented(needs_openapi):
    from responder import Path, Query

    api = responder.API(
        title="Service", version="1.0", openapi="3.0.2", allowed_hosts=[";"]
    )

    @api.route("/users/{uid}")
    class Users:
        def on_get(
            self,
            req,
            resp,
            *,
            user_id: int = Path(..., alias="uid"),
            include_deleted: bool = Query(False),
        ):
            resp.media = {"id": user_id}

        def on_post(self, req, resp):
            resp.media = {"ok": True}

    dump = yaml.safe_load(api.requests.get("/schema.yml").content)
    item = dump["paths"]["/users/{uid}"]
    get_params = {p["name"]: p for p in item["get"]["parameters"]}
    assert get_params["uid"]["schema"] == {"type": "integer"}
    assert get_params["include_deleted"]["in"] == "query"
    assert get_params["include_deleted"]["schema"] == {"type": "boolean"}
    assert item["post"]["parameters"] == [
        {
            "name": "uid",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
        }
    ]


def test_openapi_json_via_accept_header(needs_openapi):
    api = responder.API(
        title="Service", version="1.0", openapi="3.0.2", allowed_hosts=[";"]
    )

    @api.route("/x")
    def x(req, resp):
        """X.
        ---
        get:
            description: X
            responses:
                200:
                    description: ok
        """
        resp.text = "x"

    r = api.requests.get("/schema.yml", headers={"Accept": "application/json"})
    assert "application/json" in r.headers["content-type"]
    assert r.json()["openapi"] == "3.0.2"

    # Default remains YAML.
    r = api.requests.get("/schema.yml")
    assert "yaml" in r.headers["content-type"]


def test_openapi_json_route(needs_openapi):
    api = responder.API(
        title="Service",
        version="1.0",
        openapi="3.1.0",
        openapi_route="/schema.json",
        allowed_hosts=[";"],
    )

    r = api.requests.get("/schema.json")
    assert r.json()["openapi"] == "3.1.0"


# --- content-negotiated error responses ---


def test_404_json_for_legacy_json_clients(api):
    api.problem_details = False
    api.router.problem_details = False
    r = api.requests.get("/missing", headers={"Accept": "application/json"})
    assert r.status_code == 404
    assert r.json() == {"error": "Not Found"}


def test_404_plain_text_for_legacy_clients(api):
    api.problem_details = False
    api.router.problem_details = False
    r = api.requests.get("/missing")
    assert r.status_code == 404
    assert "Not Found" in r.text


def test_405_json_for_legacy_json_clients(api):
    api.problem_details = False
    api.router.problem_details = False

    @api.route("/only-get", methods=["GET"])
    def view(req, resp):
        resp.text = "ok"

    r = api.requests.post("/only-get", headers={"Accept": "application/json"})
    assert r.status_code == 405
    assert r.json() == {"error": "Method Not Allowed"}
    assert "GET" in r.headers["Allow"]


# --- duplicate route registration ---


def test_duplicate_route_raises_value_error(api):
    @api.route("/dup")
    def first(req, resp):
        resp.text = "1"

    with pytest.raises(ValueError, match="already exists"):

        @api.route("/dup")
        def second(req, resp):
            resp.text = "2"
