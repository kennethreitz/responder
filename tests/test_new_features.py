"""Tests for new features: validation, SSE, after_request, route groups, stream_file."""

import pytest
from pydantic import BaseModel
from starlette.testclient import TestClient as StarletteTestClient

import responder


# --- Pydantic auto-validation ---


class ItemIn(BaseModel):
    name: str
    price: float


class ItemOut(BaseModel):
    id: int
    name: str
    price: float


def test_pydantic_request_validation():
    """Auto-validate request body against request_model."""
    api = responder.API(allowed_hosts=[";"])

    @api.route("/items", methods=["POST"], request_model=ItemIn)
    async def create(req, resp):
        data = await req.media()
        resp.media = {"id": 1, **data}

    # Valid request
    r = api.requests.post("http://;/items", json={"name": "widget", "price": 9.99})
    assert r.status_code == 200
    assert r.json()["name"] == "widget"

    # Invalid request — missing required field
    r = api.requests.post("http://;/items", json={"name": "widget"})
    assert r.status_code == 422
    assert "errors" in r.json()

    # Invalid request — wrong type
    r = api.requests.post("http://;/items", json={"name": "widget", "price": "not_a_number"})
    assert r.status_code == 422


def test_pydantic_response_serialization():
    """Auto-serialize response through response_model."""
    api = responder.API(allowed_hosts=[";"])

    @api.route("/items", methods=["POST"],
               request_model=ItemIn, response_model=ItemOut)
    async def create(req, resp):
        data = await req.media()
        # Include an extra field that should be stripped by the model
        resp.media = {"id": 1, "secret": "hidden", **data}

    r = api.requests.post("http://;/items", json={"name": "widget", "price": 9.99})
    assert r.status_code == 200
    data = r.json()
    assert data == {"id": 1, "name": "widget", "price": 9.99}
    assert "secret" not in data


def test_pydantic_validation_skipped_for_get():
    """GET requests don't trigger request body validation."""
    api = responder.API(allowed_hosts=[";"])

    @api.route("/items", methods=["GET"], request_model=ItemIn)
    def list_items(req, resp):
        resp.media = []

    r = api.requests.get("http://;/items")
    assert r.status_code == 200


# --- SSE streaming ---


def test_sse_streaming(api):
    """Server-Sent Events with resp.sse."""

    @api.route("/events")
    async def events(req, resp):
        @resp.sse
        async def stream():
            yield {"data": "hello"}
            yield {"event": "update", "data": "world"}
            yield "simple"

    r = api.requests.get(api.url_for(events))
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")
    assert "data: hello" in r.text
    assert "event: update" in r.text
    assert "data: world" in r.text
    assert "data: simple" in r.text


def test_sse_with_id_and_retry(api):
    """SSE events with id and retry fields."""

    @api.route("/events")
    async def events(req, resp):
        @resp.sse
        async def stream():
            yield {"data": "msg", "id": "1", "retry": "5000"}

    r = api.requests.get(api.url_for(events))
    assert "id: 1" in r.text
    assert "retry: 5000" in r.text


# --- stream_file ---


def test_stream_file(api, tmp_path):
    """Stream a file without loading into memory."""
    big_file = tmp_path / "data.bin"
    big_file.write_bytes(b"x" * 10000)

    @api.route("/download")
    def download(req, resp):
        resp.stream_file(big_file)

    r = api.requests.get(api.url_for(download))
    assert len(r.content) == 10000
    assert r.content == b"x" * 10000


def test_stream_file_content_type(api, tmp_path):
    """stream_file detects content type."""
    css = tmp_path / "style.css"
    css.write_text("body { color: red; }")

    @api.route("/css")
    def serve_css(req, resp):
        resp.stream_file(css)

    r = api.requests.get(api.url_for(serve_css))
    assert "text/css" in r.headers.get("content-type", "")


# --- after_request hooks ---


def test_after_request(api):
    """after_request hook runs after route handler."""

    @api.after_request()
    def add_header(req, resp):
        resp.headers["X-After"] = "yes"

    @api.route("/")
    def view(req, resp):
        resp.text = "hello"

    r = api.requests.get(api.url_for(view))
    assert r.text == "hello"
    assert r.headers["X-After"] == "yes"


def test_after_request_async(api):
    """Async after_request hook."""

    @api.after_request()
    async def add_header(req, resp):
        resp.headers["X-Async-After"] = "yes"

    @api.route("/")
    def view(req, resp):
        resp.text = "hello"

    r = api.requests.get(api.url_for(view))
    assert r.headers["X-Async-After"] == "yes"


# --- Route groups ---


def test_route_group(api):
    """Route group with shared prefix."""
    v1 = api.group("/v1")

    @v1.route("/users")
    def list_users(req, resp):
        resp.media = [{"name": "alice"}]

    @v1.route("/users/{user_id:int}")
    def get_user(req, resp, *, user_id):
        resp.media = {"id": user_id}

    r = api.requests.get("http://;/v1/users")
    assert r.json() == [{"name": "alice"}]

    r = api.requests.get("http://;/v1/users/42")
    assert r.json() == {"id": 42}


def test_multiple_route_groups(api):
    """Multiple route groups coexist."""
    v1 = api.group("/v1")
    v2 = api.group("/v2")

    @v1.route("/status")
    def v1_status(req, resp):
        resp.media = {"version": 1}

    @v2.route("/status")
    def v2_status(req, resp):
        resp.media = {"version": 2}

    assert api.requests.get("http://;/v1/status").json() == {"version": 1}
    assert api.requests.get("http://;/v2/status").json() == {"version": 2}
