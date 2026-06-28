"""v5.1: HTTP verb shortcut decorators + websocket_route."""

import yaml
from starlette.testclient import TestClient


def _client(api):
    return TestClient(api, base_url="http://;")


def test_get_and_post_on_same_path(api):
    @api.get("/items")
    def list_items(req, resp):
        resp.media = {"items": []}

    @api.post("/items")
    def create_item(req, resp):
        resp.media = {"created": True}

    client = _client(api)
    assert client.get("/items").json() == {"items": []}
    assert client.post("/items").json() == {"created": True}


def test_verb_decorator_restricts_method(api):
    @api.get("/only-get")
    def only_get(req, resp):
        resp.text = "ok"

    client = _client(api)
    assert client.get("/only-get").status_code == 200
    r = client.post("/only-get")
    assert r.status_code == 405
    assert "GET" in r.headers["allow"]


def test_put_patch_delete(api):
    @api.put("/r")
    def put(req, resp):
        resp.text = "put"

    @api.patch("/r")
    def patch(req, resp):
        resp.text = "patch"

    @api.delete("/r")
    def delete(req, resp):
        resp.text = "delete"

    client = _client(api)
    assert client.put("/r").text == "put"
    assert client.patch("/r").text == "patch"
    assert client.delete("/r").text == "delete"


def test_options_lists_all_registered_methods(api):
    @api.get("/m")
    def g(req, resp):
        resp.text = "g"

    @api.post("/m")
    def p(req, resp):
        resp.text = "p"

    allow = _client(api).options("/m").headers["allow"]
    methods = {m.strip() for m in allow.split(",")}
    assert {"GET", "POST", "HEAD", "OPTIONS"} <= methods


def test_duplicate_same_method_still_raises(api):
    @api.get("/dup")
    def first(req, resp):
        resp.text = "1"

    try:
        @api.get("/dup")
        def second(req, resp):
            resp.text = "2"
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("duplicate GET on the same path should raise")


def test_verb_decorator_on_group(api):
    g = api.group("/api")

    @g.get("/ping")
    def ping(req, resp):
        resp.text = "pong"

    assert _client(api).get("/api/ping").text == "pong"


def test_verb_decorators_split_into_openapi_operations():
    import responder

    api = responder.API(
        title="T", version="1", openapi="3.0.2",
        allowed_hosts=[";"], session_https_only=False,
    )

    @api.get("/items")
    def list_items(req, resp):
        resp.media = {"items": []}

    @api.post("/items")
    def create_item(req, resp):
        resp.media = {"created": True}

    spec = yaml.safe_load(_client(api).get("/schema.yml").content)
    ops = spec["paths"]["/items"]
    assert "get" in ops
    assert "post" in ops


def test_websocket_route(api):
    @api.websocket_route("/ws")
    async def ws(ws):
        await ws.accept()
        await ws.send_text("hi")
        await ws.close()

    client = TestClient(api)
    with client.websocket_connect("ws://;/ws") as conn:
        assert conn.receive_text() == "hi"
