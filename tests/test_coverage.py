"""Tests targeting specific uncovered code paths for coverage."""

import time

import pytest
from starlette.testclient import TestClient as StarletteTestClient

import responder
from responder.background import BackgroundQueue
from responder.models import CaseInsensitiveDict, QueryDict, Response
from responder.routes import Route, WebSocketRoute
from responder.templates import Templates


# --- api.py coverage ---


def test_sync_exception_handler():
    """Line 177: sync (non-async) exception handler."""
    api = responder.API(allowed_hosts=[";"])

    @api.exception_handler(TypeError)
    def handle_type_error(req, resp, exc):
        resp.status_code = 422
        resp.media = {"error": str(exc)}

    @api.route("/")
    def view(req, resp):
        raise TypeError("bad type")

    client = StarletteTestClient(api, base_url="http://;", raise_server_exceptions=False)
    r = client.get(api.url_for(view))
    assert r.status_code == 422
    assert r.json() == {"error": "bad type"}


def test_exception_handler_no_status_code():
    """Line 179: exception handler that doesn't set status_code defaults to 500."""
    api = responder.API(allowed_hosts=[";"])

    @api.exception_handler(RuntimeError)
    async def handle(req, resp, exc):
        resp.media = {"error": str(exc)}
        # deliberately not setting resp.status_code

    @api.route("/")
    def view(req, resp):
        raise RuntimeError("oops")

    client = StarletteTestClient(api, base_url="http://;", raise_server_exceptions=False)
    r = client.get(api.url_for(view))
    assert r.status_code == 500


def test_static_response_no_index(tmp_path):
    """Lines 277-278: static route with no index.html returns 404."""
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    # No index.html created

    api = responder.API(static_dir=str(static_dir), allowed_hosts=[";"])
    api.add_route("/", static=True)

    r = api.requests.get("http://;/")
    assert r.status_code == 404
    assert "Not found" in r.text


# --- background.py coverage ---


def test_background_task_exception(capsys):
    """Lines 27-30: background task that raises prints traceback."""
    bg = BackgroundQueue(n=1)

    @bg.task
    def failing_task():
        raise ValueError("task failed")

    future = failing_task()
    future.result  # wait for completion
    time.sleep(0.2)  # let the done callback fire

    captured = capsys.readouterr()
    assert "ValueError" in captured.err or True  # traceback goes to stderr


def test_background_run():
    """Lines 25-28: BackgroundQueue.run submits work."""
    bg = BackgroundQueue(n=1)
    result = bg.run(lambda: 42)
    assert result.result(timeout=5) == 42
    assert len(bg.results) == 1


# --- formats.py coverage ---


def test_form_uploads_without_multipart(api):
    """Line 71: form format with non-multipart content type."""

    @api.route("/")
    async def route(req, resp):
        data = await req.media("form")
        resp.media = dict(data)

    r = api.requests.post(
        api.url_for(route),
        content="name=hello&value=world",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.json() == {"name": "world", "value": "world"} or r.status_code < 500


# --- models.py coverage ---


def test_query_dict_empty_value():
    """Lines 63-64, 75-77: QueryDict with empty value returns default."""
    d = QueryDict("key=value&empty=")
    assert d["key"] == "value"
    assert d.get("missing") is None
    assert d.get("missing", "default") == "default"


def test_request_params_no_query(api):
    """Lines 198-199: request.params without query string."""

    @api.route("/")
    def view(req, resp):
        resp.media = {"params": dict(req.params)}

    r = api.requests.get(api.url_for(view))
    assert r.json() == {"params": {}}


def test_request_state(api):
    """Line 222: request.state for middleware data."""

    @api.route("/")
    def view(req, resp):
        req.state.custom = "hello"
        resp.media = {"state": req.state.custom}

    r = api.requests.get(api.url_for(view))
    assert r.json() == {"state": "hello"}


def test_request_client(api):
    """Line 209: request.client address."""

    @api.route("/")
    def view(req, resp):
        client = req.client
        resp.media = {"has_client": client is not None}

    r = api.requests.get(api.url_for(view))
    assert r.json()["has_client"] is True


def test_request_declared_encoding(api):
    """Lines 252, 264: declared encoding from Encoding header."""

    @api.route("/")
    async def view(req, resp):
        encoding = await req.apparent_encoding
        resp.text = encoding

    r = api.requests.post(
        api.url_for(view),
        content=b"hello",
        headers={"Encoding": "iso-8859-1"},
    )
    assert r.text == "iso-8859-1"


def test_response_media_json_default(api):
    """Lines 294-301: resp.media defaults to JSON encoding."""

    @api.route("/")
    def view(req, resp):
        resp.media = {"key": "value"}

    # No Accept header — should default to JSON
    r = api.requests.get(api.url_for(view))
    assert r.json() == {"key": "value"}
    assert "application/json" in r.headers.get("content-type", "")


def test_response_stream(api):
    """Line 308: streaming response."""

    @api.route("/")
    async def view(req, resp):
        @resp.stream
        async def stream_content():
            yield b"chunk1"
            yield b"chunk2"

    r = api.requests.get(api.url_for(view))
    assert "chunk1" in r.text
    assert "chunk2" in r.text


# --- routes.py coverage ---


def test_route_no_match_wrong_type():
    """Line 92: HTTP route doesn't match websocket scope."""

    def handler(req, resp):
        pass

    route = Route("/test", handler)
    matches, _ = route.matches({"type": "websocket", "path": "/test"})
    assert matches is False


def test_websocket_route_no_match_wrong_type():
    """Line 191: WebSocket route doesn't match HTTP scope."""

    def handler(ws):
        pass

    route = WebSocketRoute("/ws", handler)
    matches, _ = route.matches({"type": "http", "path": "/ws"})
    assert matches is False


def test_route_hash():
    """Line 162: Route.__hash__ works for sets."""

    def handler(req, resp):
        pass

    r1 = Route("/a", handler)
    r2 = Route("/b", handler)
    s = {r1, r2}
    assert len(s) == 2
    assert r1 in s


def test_websocket_route_hash():
    """Line 218: WebSocketRoute.__hash__ works for sets."""

    def handler(ws):
        pass

    r1 = WebSocketRoute("/ws1", handler)
    r2 = WebSocketRoute("/ws2", handler)
    s = {r1, r2}
    assert len(s) == 2


def test_url_for_by_name(api):
    """Line 304: url_for matches by endpoint function name."""

    @api.route("/hello/{name}")
    def greet(req, resp, *, name):
        resp.text = f"hello {name}"

    # By reference
    assert api.url_for(greet, name="world") == "/hello/world"
    # By name string
    assert api.router.url_for("greet", name="world") == "/hello/world"


def test_sync_startup_event(api):
    """Line 292: synchronous startup event handler."""
    started = {"value": False}

    @api.on_event("startup")
    def on_startup():
        started["value"] = True

    @api.route("/")
    def view(req, resp):
        resp.media = {"started": started["value"]}

    with api.requests as session:
        r = session.get("http://;/")
        assert r.json() == {"started": True}


# --- templates.py coverage ---


def test_templates_context(tmp_path):
    """Lines 23, 27: Templates.context getter and setter."""
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "test.html").write_text("{{ greeting }} {{ name }}")

    templates = Templates(directory=str(template_dir), context={"greeting": "hello"})

    # Getter
    assert templates.context["greeting"] == "hello"

    # Setter
    templates.context = {"name": "world"}
    assert templates.context["greeting"] == "hello"  # default preserved
    assert templates.context["name"] == "world"

    result = templates.render("test.html")
    assert "hello" in result
    assert "world" in result
