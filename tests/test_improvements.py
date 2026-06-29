"""Tests for route matching fixes, per-route rate limiting, WebSocket
short-circuiting, and custom format registration."""

import pytest
from starlette.exceptions import HTTPException
from starlette.testclient import TestClient as StarletteTestClient
from starlette.websockets import WebSocketDisconnect

from responder import Depends
from responder.ext.ratelimit import RateLimiter

# --- route matching fixes ---


def test_float_convertor_rejects_garbage(api):
    @api.route("/measure/{value:float}")
    def view(req, resp, *, value):
        resp.media = {"value": value}

    assert api.requests.get("/measure/1.5").json() == {"value": 1.5}
    assert api.requests.get("/measure/10").json() == {"value": 10.0}
    # "1a5" must not match the float pattern (previously the unescaped
    # dot allowed any character and the convertor crashed with a 500).
    assert api.requests.get("/measure/1a5").status_code == 404


def test_literal_dots_in_routes_are_escaped(api):
    @api.route("/file.json")
    def view(req, resp):
        resp.text = "ok"

    assert api.requests.get("/file.json").status_code == 200
    # A literal "." in the route must not act as a regex wildcard.
    assert api.requests.get("/fileXjson").status_code == 404


# --- per-route rate limiting ---


def test_per_route_rate_limit(api):
    limiter = RateLimiter(requests=2, period=60)

    @api.route("/limited")
    @limiter.limit
    def limited(req, resp):
        resp.text = "ok"

    @api.route("/open")
    def unlimited(req, resp):
        resp.text = "always"

    assert api.requests.get("/limited").status_code == 200
    assert api.requests.get("/limited").status_code == 200
    third = api.requests.get("/limited")
    assert third.status_code == 429
    assert "Retry-After" in third.headers

    # Other routes are unaffected.
    assert api.requests.get("/open").status_code == 200


def test_per_route_rate_limit_async(api):
    limiter = RateLimiter(requests=1, period=60)

    @api.route("/limited")
    @limiter.limit
    async def limited(req, resp):
        resp.text = "ok"

    assert api.requests.get("/limited").status_code == 200
    assert api.requests.get("/limited").status_code == 429


# --- WebSocket before_request short-circuit ---


def test_websocket_before_request_short_circuit(api):
    endpoint_called = []

    @api.before_request(websocket=True)
    async def reject_unauthorized(ws):
        if "Authorization" not in ws.headers:
            await ws.close(code=4401)

    @api.route("/ws", websocket=True)
    async def ws_endpoint(ws):
        endpoint_called.append(True)
        await ws.accept()
        await ws.send_text("hello")
        await ws.close()

    client = StarletteTestClient(api)

    # Without auth: the hook closes the socket and the endpoint never runs.
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("ws://;/ws"):
            pass
    assert endpoint_called == []

    # With auth: the connection proceeds normally.
    with client.websocket_connect(
        "ws://;/ws", headers={"Authorization": "Bearer token"}
    ) as ws:
        assert ws.receive_text() == "hello"
    assert endpoint_called == [True]


def test_websocket_sync_before_request(api):
    seen = []

    @api.before_request(websocket=True)
    def observe(ws):
        seen.append(ws.url.path)

    @api.route("/ws", websocket=True)
    async def ws_endpoint(ws):
        await ws.accept()
        await ws.send_text("hi")
        await ws.close()

    client = StarletteTestClient(api)
    with client.websocket_connect("ws://;/ws") as ws:
        assert ws.receive_text() == "hi"
    assert seen == ["/ws"]


def test_websocket_route_before_auth_dependencies_order(api):
    events = []

    @api.before_request(websocket=True)
    def global_before(_):
        events.append("global-before")

    def route_before(_):
        events.append("route-before")

    def require_api_key(req):
        events.append("auth")
        if req.headers.get("Authorization") != "secret":
            raise HTTPException(status_code=401, detail="No auth")
        return "api-key"

    def require_token(ws):
        events.append("dependency")
        return ws.headers.get("x-token")

    @api.route(
        "/ws",
        websocket=True,
        before=route_before,
        auth=require_api_key,
        dependencies=[Depends(require_token)],
    )
    async def ws_endpoint(ws, *, user):
        events.append("handler")
        await ws.accept()
        await ws.send_text(user)
        await ws.close()

    client = StarletteTestClient(api)
    with client.websocket_connect(
        "ws://;/ws", headers={"Authorization": "secret", "x-token": "abc"}
    ) as ws:
        assert ws.receive_text() == "api-key"
    assert events == [
        "global-before",
        "route-before",
        "auth",
        "dependency",
        "handler",
    ]


def test_websocket_route_before_hooks_short_circuit(api):
    events = []

    @api.before_request(websocket=True)
    async def global_before(ws):
        events.append("global-before")
        await ws.close(code=4401)

    def route_before(ws):
        events.append("route-before")

    def require_api_key(req):
        events.append("auth")
        return req.headers.get("Authorization")

    def require_token(ws):
        events.append("dependency")
        return ws.headers.get("x-token")

    @api.route(
        "/ws",
        websocket=True,
        before=route_before,
        auth=require_api_key,
        dependencies=[Depends(require_token)],
    )
    async def ws_endpoint(ws):
        events.append("handler")

    client = StarletteTestClient(api)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("ws://;/ws"):
            pass

    assert events == ["global-before"]


# --- custom formats ---


def test_custom_format_registration(api):
    async def format_csv(r, encode=False):
        if encode:
            r.headers["Content-Type"] = "text/csv"
            rows = r.media
            return "\n".join(",".join(str(v) for v in row) for row in rows)
        return [line.split(",") for line in (await r.text).splitlines()]

    api.formats["csv"] = format_csv

    @api.route("/report")
    def report(req, resp):
        resp.media = [["a", 1], ["b", 2]]

    r = api.requests.get("/report", headers={"Accept": "text/csv"})
    assert r.headers["Content-Type"].startswith("text/csv")
    assert r.text == "a,1\nb,2"


def test_form_accept_header_falls_back_to_json(api):
    @api.route("/data")
    def data(req, resp):
        resp.media = {"key": "value"}

    # "form" can't encode responses; negotiation should fall through to JSON
    # instead of returning an empty body.
    r = api.requests.get("/data", headers={"Accept": "multipart/form-data"})
    assert r.json() == {"key": "value"}
