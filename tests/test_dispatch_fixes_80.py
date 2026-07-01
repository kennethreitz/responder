"""Dispatch fixes for responder 8.0 (routes.py):

- ``add_route(default=True)`` with a Responder-signature view no longer crashes
- HEAD falls back to ``on_get`` on class-based views
- WSGI mounts are wrapped once, not per request
- a mount prefix with a trailing slash matches subpaths
- unhandled WebSocket handler exceptions close the socket with 1011
- two distinct providers sharing a ``__name__`` no longer trip cycle detection
- ``url_for`` raises ``RouteNotFoundError`` for unknown endpoints and
  URL-quotes parameter values
- a user ``lifespan=`` context manager cooperates with ``on_event`` handlers
"""

import asyncio
import time
from contextlib import asynccontextmanager

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import responder
from responder import Depends
from responder.routes import DependencyCycleError, RouteNotFoundError, Router


def _api(**kwargs):
    kwargs.setdefault("allowed_hosts", [";"])
    kwargs.setdefault("session_https_only", False)
    return responder.API(**kwargs)


def _wsgi_app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"wsgi-ok"]


async def _asgi_app(scope, receive, send):
    body = f"asgi:{scope['path']}".encode()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": body})


# --- 1. add_route(default=True) with a (req, resp) view ---


def test_default_route_with_responder_view(api):
    def not_found(req, resp):
        resp.status_code = 404
        resp.text = "custom not found"

    api.add_route("/fallback", not_found, default=True)

    r = api.requests.get("/definitely-not-a-route")
    assert r.status_code == 404
    assert r.text == "custom not found"

    # And the same view still answers at its own path.
    r = api.requests.get("/fallback")
    assert r.status_code == 404
    assert r.text == "custom not found"


def test_default_route_runs_hooks_like_a_regular_route(api):
    seen = []

    @api.after_request
    def tag(req, resp):
        seen.append(req.url.path)
        resp.headers["X-Tagged"] = "yes"

    def fallback(req, resp):
        resp.text = "fell back"

    api.add_route("/fallback", fallback, default=True)

    r = api.requests.get("/nowhere")
    assert r.text == "fell back"
    assert r.headers["X-Tagged"] == "yes"
    assert "/nowhere" in seen


def test_internal_asgi_default_still_404s(api):
    @api.route("/known")
    def known(req, resp):
        resp.text = "ok"

    assert api.requests.get("/unknown").status_code == 404


def test_unmatched_websocket_still_closed_with_view_default(api):
    def fallback(req, resp):
        resp.text = "http only"

    api.add_route("/fallback", fallback, default=True)

    client = TestClient(api, base_url="http://;")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("ws://;/no-such-socket"):
            pass  # pragma: no cover - connection is rejected


# --- 2. HEAD falls back to on_get on class-based views ---


def test_head_falls_back_to_on_get_for_class_views(api):
    @api.route("/box")
    class Box:
        def on_get(self, req, resp):
            resp.text = "contents"
            resp.headers["X-Box"] = "full"

    r = api.requests.head("/box")
    assert r.status_code == 200
    assert r.headers["X-Box"] == "full"
    assert r.text == ""  # HEAD strips the body


def test_explicit_on_head_still_wins(api):
    @api.route("/box")
    class Box:
        def on_get(self, req, resp):
            resp.headers["X-Handler"] = "get"

        def on_head(self, req, resp):
            resp.headers["X-Handler"] = "head"

    r = api.requests.head("/box")
    assert r.status_code == 200
    assert r.headers["X-Handler"] == "head"


def test_class_view_without_matching_method_still_405s(api):
    @api.route("/box")
    class Box:
        def on_get(self, req, resp):
            resp.text = "contents"

    assert api.requests.post("/box").status_code == 405


# --- 3. WSGI mounts are wrapped once, not per request ---


def test_wsgi_mount_wraps_middleware_once(api, monkeypatch):
    import a2wsgi

    real = a2wsgi.WSGIMiddleware
    wraps = []

    def counting(app, *args, **kwargs):
        wraps.append(app)
        return real(app, *args, **kwargs)

    monkeypatch.setattr(a2wsgi, "WSGIMiddleware", counting)

    api.mount("/wsgi", _wsgi_app)
    for _ in range(3):
        r = api.requests.get("/wsgi/")
        assert r.status_code == 200
        assert "wsgi-ok" in r.text
    assert len(wraps) == 1


def test_router_mount_wraps_wsgi_at_mount_time(monkeypatch):
    import a2wsgi

    real = a2wsgi.WSGIMiddleware
    wraps = []

    def counting(app, *args, **kwargs):
        wraps.append(app)
        return real(app, *args, **kwargs)

    monkeypatch.setattr(a2wsgi, "WSGIMiddleware", counting)

    router = Router()
    router.mount("/wsgi", _wsgi_app)
    assert len(wraps) == 1


# --- 4. mount prefix with a trailing slash matches subpaths ---


def test_mount_prefix_with_trailing_slash_matches_subpaths(api):
    api.mount("/admin/", _asgi_app)

    r = api.requests.get("/admin/panel")
    assert r.status_code == 200
    assert r.text == "asgi:/panel"

    r = api.requests.get("/admin")
    assert r.status_code == 200
    assert r.text == "asgi:/"


def test_root_mount_still_catches_everything(api):
    api.mount("", _asgi_app)

    r = api.requests.get("/anything/at/all")
    assert r.status_code == 200
    assert r.text == "asgi:/anything/at/all"


# --- 5. unhandled WebSocket handler exceptions close with 1011 ---


def _ws_scope(path):
    return {
        "type": "websocket",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "scheme": "ws",
        "query_string": b"",
        "headers": [(b"host", b";")],
        "client": ("testclient", 50000),
        "server": (";", 80),
        "subprotocols": [],
    }


def test_ws_handler_exception_sends_1011_close(api):
    # Drive the router at the ASGI level: the TestClient tears its streams
    # down when the app task raises, so the close frame is asserted here.
    @api.route("/boom", websocket=True)
    async def boom(ws):
        await ws.accept()
        raise RuntimeError("kapow")

    sent = []
    incoming = [{"type": "websocket.connect"}]

    async def receive():
        return incoming.pop(0)

    async def send(message):
        sent.append(message)

    with pytest.raises(RuntimeError, match="kapow"):
        asyncio.run(api.router(_ws_scope("/boom"), receive, send))

    assert sent[0]["type"] == "websocket.accept"
    assert sent[-1] == {"type": "websocket.close", "code": 1011, "reason": ""}


def test_ws_handler_exception_still_reraises_to_the_client(api):
    @api.route("/boom", websocket=True)
    async def boom(ws):
        await ws.accept()
        await ws.receive_text()
        raise RuntimeError("kapow")

    client = TestClient(api, base_url="http://;")
    with pytest.raises(RuntimeError, match="kapow"):
        with client.websocket_connect("ws://;/boom") as ws:
            ws.send_text("go")
            ws.receive_text()


def test_ws_normal_close_is_untouched(api):
    @api.route("/fine", websocket=True)
    async def fine(ws):
        await ws.accept()
        await ws.send_text("hello")
        await ws.close(code=1000)

    client = TestClient(api, base_url="http://;")
    with client.websocket_connect("ws://;/fine") as ws:
        assert ws.receive_text() == "hello"
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_text()
        assert excinfo.value.code == 1000


# --- 6. providers sharing a __name__ don't trip cycle detection ---


def _make_inner_get_db():
    def get_db():
        return "inner"

    return get_db


def test_distinct_providers_with_same_name_are_not_a_cycle(api):
    inner = _make_inner_get_db()

    def get_db(db=Depends(inner)):
        return f"outer({db})"

    @api.route("/db")
    def view(req, resp, *, value=Depends(get_db)):
        resp.text = value

    r = api.requests.get("/db")
    assert r.status_code == 200
    assert r.text == "outer(inner)"


def test_two_inline_lambda_depends_are_not_a_cycle(api):
    @api.route("/pair")
    def view(req, resp, *, a=Depends(lambda: "a"), b=Depends(lambda: "b")):
        resp.text = a + b

    # Nested: a lambda provider depending on another lambda provider.
    def outer(x=Depends(lambda: "x")):
        return x + "y"

    @api.route("/nested")
    def nested(req, resp, *, v=Depends(outer)):
        resp.text = v

    assert api.requests.get("/pair").text == "ab"
    assert api.requests.get("/nested").text == "xy"


def test_genuine_provider_cycle_is_still_detected(api):
    def a(v=None):
        return v

    def b(v=Depends(a)):
        return v

    # Late-bind a -> b, creating a real a <-> b cycle.
    a.__defaults__ = (Depends(b),)

    @api.route("/cycle")
    def view(req, resp, *, value=Depends(a)):
        resp.text = str(value)

    with pytest.raises(DependencyCycleError):
        api.requests.get("/cycle")


# --- 7. url_for raises for unknown endpoints; values are URL-quoted ---


def test_url_for_unknown_endpoint_raises_lookup_error(api):
    with pytest.raises(RouteNotFoundError):
        api.url_for("no-such-route")
    with pytest.raises(LookupError):
        api.url_for(lambda req, resp: None)


def test_url_for_known_routes_still_work(api):
    @api.route("/users/{id:int}", name="user_detail")
    def user_detail(req, resp, id):
        resp.text = str(id)

    assert api.url_for(user_detail, id=42) == "/users/42"
    assert api.url_for("user_detail", id=42) == "/users/42"


def test_url_for_quotes_parameter_values(api):
    @api.route("/file/{name}")
    def file_view(req, resp, name):
        resp.text = name

    # Non-path parameter values are fully percent-encoded. Note this string is
    # NOT a matchable URL: the server percent-decodes %2F back to "/" before
    # routing, and the default [^/]+ segment can never contain a slash.
    assert api.url_for(file_view, name="a b/c") == "/file/a%20b%2Fc"


def test_url_for_round_trip_behavior_for_default_params(api):
    @api.route("/file/{name}")
    def file_view(req, resp, name):
        resp.text = name

    # A value with a space round-trips: quoted on the way out, decoded by the
    # server, and matched by the default segment pattern.
    url = api.url_for(file_view, name="a b")
    assert url == "/file/a%20b"
    r = api.requests.get(url)
    assert r.status_code == 200
    assert r.text == "a b"

    # A value with an embedded slash cannot round-trip for a default
    # (non-:path) parameter: %2F decodes to "/" before matching, so the
    # generated URL 404s. Use a {param:path} convertor for such values.
    assert api.requests.get(api.url_for(file_view, name="a b/c")).status_code == 404


def test_url_for_skips_callable_instance_endpoints_without_a_name(api):
    class InstanceView:
        def __call__(self, req, resp):
            resp.text = "instance"

    instance = InstanceView()
    api.add_route("/instance", instance)

    @api.route("/fn")
    def fn(req, resp):
        resp.text = "fn"

    # The nameless instance route must not break the fallback scan.
    assert api.url_for(fn) == "/fn"
    # Lookup by the endpoint object itself still works.
    assert api.url_for(instance) == "/instance"
    # Unknown names raise RouteNotFoundError, not AttributeError.
    with pytest.raises(RouteNotFoundError):
        api.url_for("missing")


def test_url_for_path_convertor_keeps_slashes(api):
    @api.route("/tree/{rest:path}")
    def tree(req, resp, rest):
        resp.text = rest

    assert api.url_for(tree, rest="a b/c") == "/tree/a%20b/c"


def test_websocket_url_is_quoted(api):
    @api.route("/ws/{room}", websocket=True)
    async def room_socket(ws, room):
        await ws.accept()
        await ws.close()

    assert api.url_for(room_socket, room="a b") == "/ws/a%20b"


# --- 8. lifespan= cooperates with on_event handlers ---


def test_lifespan_and_on_event_both_fire_in_order():
    events = []

    @asynccontextmanager
    async def lifespan(app):
        events.append("ctx-enter")
        yield
        events.append("ctx-exit")

    api = _api(lifespan=lifespan)

    @api.on_event("startup")
    async def startup():
        events.append("startup")

    @api.on_event("shutdown")
    async def shutdown():
        events.append("shutdown")

    with api.requests:
        pass

    assert events == ["ctx-enter", "startup", "shutdown", "ctx-exit"]


def test_background_tasks_drain_on_shutdown_with_lifespan():
    done = []

    @asynccontextmanager
    async def lifespan(app):
        yield

    api = _api(lifespan=lifespan)

    @api.route("/work")
    def work(req, resp):
        @api.background.task
        def slow():
            time.sleep(0.2)
            done.append(True)

        slow()
        resp.text = "queued"

    with api.requests as session:
        assert session.get("/work").text == "queued"

    assert done == [True]


def test_raising_aexit_still_tears_down_app_dependencies():
    torn = []

    @asynccontextmanager
    async def lifespan(app):
        yield
        raise RuntimeError("exit boom")

    api = _api(lifespan=lifespan)

    def resource():
        yield "the-resource"
        torn.append(True)

    api.add_dependency("resource", resource, scope="app")

    @api.route("/use")
    def use(req, resp, resource):
        resp.text = resource

    with pytest.raises(RuntimeError, match="exit boom"):
        with api.requests as session:
            assert session.get("/use").text == "the-resource"

    assert torn == [True]


def test_raising_aexit_reports_lifespan_shutdown_failed():
    @asynccontextmanager
    async def lifespan(app):
        yield
        raise RuntimeError("exit boom")

    api = _api(lifespan=lifespan)

    sent = []
    incoming = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]

    async def receive():
        return incoming.pop(0)

    async def send(message):
        sent.append(message)

    with pytest.raises(RuntimeError, match="exit boom"):
        asyncio.run(api.router.lifespan({"type": "lifespan"}, receive, send))

    types = [message["type"] for message in sent]
    assert "lifespan.startup.complete" in types
    assert "lifespan.shutdown.failed" in types


def test_lifespan_startup_event_failure_reports_startup_failed():
    exited = []

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            exited.append(True)

    api = _api(lifespan=lifespan)

    @api.on_event("startup")
    async def bad_startup():
        raise RuntimeError("startup boom")

    sent = []
    incoming = [{"type": "lifespan.startup"}]

    async def receive():
        return incoming.pop(0)

    async def send(message):
        sent.append(message)

    with pytest.raises(RuntimeError, match="startup boom"):
        asyncio.run(api.router.lifespan({"type": "lifespan"}, receive, send))

    assert sent[-1]["type"] == "lifespan.startup.failed"
    # The already-entered context manager is unwound.
    assert exited == [True]
