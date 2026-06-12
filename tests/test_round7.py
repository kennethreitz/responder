"""Tests for built-in metrics, server-side sessions, query-param validation,
and resp.render()."""

import json

import pytest
import yaml

import responder
from responder.ext.sessions import MemorySessionBackend, RedisSessionBackend

# --- metrics ---


def test_metrics_endpoint_counts_requests():
    api = responder.API(metrics_route="/metrics", allowed_hosts=[";"])

    @api.route("/items/{id:int}")
    def item(req, resp, *, id):
        resp.media = {"id": id}

    api.requests.get("/items/1")
    api.requests.get("/items/2")
    api.requests.get("/nowhere")

    r = api.requests.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.text

    # Counts are labelled by route pattern, not raw path.
    assert (
        'responder_requests_total{method="GET",path="/items/{id}",status="200"} 2'
        in body
    )
    # 404s are labelled "unmatched" to keep cardinality bounded.
    assert (
        'responder_requests_total{method="GET",path="unmatched",status="404"} 1' in body
    )
    # Latency histogram series exist.
    assert 'responder_request_duration_seconds_bucket{method="GET"' in body
    assert 'le="+Inf"' in body
    assert "responder_request_duration_seconds_sum" in body


def test_metrics_disabled_by_default(api):
    @api.route("/x")
    def x(req, resp):
        resp.text = "ok"

    assert api.requests.get("/metrics").status_code == 404


# --- server-side sessions ---


def test_memory_session_round_trip():
    api = responder.API(session_backend=MemorySessionBackend(), allowed_hosts=[";"])

    @api.route("/login", methods=["POST"])
    def login(req, resp):
        req.session["user"] = "kenneth"
        resp.media = {"ok": True}

    @api.route("/whoami")
    def whoami(req, resp):
        resp.media = {"user": req.session.get("user")}

    client = api.requests
    assert client.get("/whoami").json() == {"user": None}

    r = client.post("/login")
    assert "responder_session=" in r.headers["set-cookie"]
    # The cookie holds an opaque ID, not the data.
    assert "kenneth" not in r.headers["set-cookie"]

    assert client.get("/whoami").json() == {"user": "kenneth"}


def test_memory_session_logout_clears():
    api = responder.API(session_backend=MemorySessionBackend(), allowed_hosts=[";"])

    @api.route("/login", methods=["POST"])
    def login(req, resp):
        req.session["user"] = "kenneth"
        resp.media = {"ok": True}

    @api.route("/logout", methods=["POST"])
    def logout(req, resp):
        req.session.clear()
        resp.media = {"ok": True}

    @api.route("/whoami")
    def whoami(req, resp):
        resp.media = {"user": req.session.get("user")}

    client = api.requests
    client.post("/login")
    assert client.get("/whoami").json() == {"user": "kenneth"}

    r = client.post("/logout")
    assert "Max-Age=0" in r.headers["set-cookie"]
    assert client.get("/whoami").json() == {"user": None}


def test_memory_session_expiry():
    backend = MemorySessionBackend()
    backend.set("sid", {"user": "k"}, max_age=-1)  # already expired
    assert backend.get("sid") is None


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, max_age, value):
        self.store[key] = value

    def delete(self, key):
        self.store.pop(key, None)


def test_redis_session_backend():
    fake = FakeRedis()
    api = responder.API(
        session_backend=RedisSessionBackend(client=fake), allowed_hosts=[";"]
    )

    @api.route("/login", methods=["POST"])
    def login(req, resp):
        req.session["cart"] = [1, 2, 3]
        resp.media = {"ok": True}

    @api.route("/cart")
    def cart(req, resp):
        resp.media = {"cart": req.session.get("cart")}

    client = api.requests
    client.post("/login")
    assert client.get("/cart").json() == {"cart": [1, 2, 3]}

    # Stored under the prefix, as JSON.
    (key,) = fake.store
    assert key.startswith("responder:session:")
    assert json.loads(fake.store[key]) == {"cart": [1, 2, 3]}


# --- query-param validation ---


def test_params_model_validates_and_coerces(api):
    from pydantic import BaseModel

    class SearchParams(BaseModel):
        q: str
        limit: int = 10

    @api.route("/search", params_model=SearchParams)
    def search(req, resp):
        params = req.state.validated_params
        resp.media = {"q": params.q, "limit": params.limit}

    r = api.requests.get("/search?q=hello&limit=5")
    assert r.json() == {"q": "hello", "limit": 5}

    # Defaults apply.
    assert api.requests.get("/search?q=x").json() == {"q": "x", "limit": 10}


def test_params_model_invalid_returns_422(api):
    from pydantic import BaseModel

    class SearchParams(BaseModel):
        q: str
        limit: int = 10

    # Missing required param.
    @api.route("/search", params_model=SearchParams)
    def search(req, resp):
        resp.text = "never"

    r = api.requests.get("/search")
    assert r.status_code == 422
    assert "errors" in r.json()

    # Uncoercible value.
    r = api.requests.get("/search?q=x&limit=banana")
    assert r.status_code == 422


def test_params_model_multi_value_list(api):
    from pydantic import BaseModel

    class FilterParams(BaseModel):
        tag: list[str]

    @api.route("/filter", params_model=FilterParams)
    def filter_view(req, resp):
        resp.media = {"tags": req.state.validated_params.tag}

    r = api.requests.get("/filter?tag=a&tag=b")
    assert r.json() == {"tags": ["a", "b"]}


def test_params_model_documented_in_openapi(needs_openapi):
    from pydantic import BaseModel

    class SearchParams(BaseModel):
        q: str
        limit: int = 10

    class Out(BaseModel):
        results: list[str]

    api = responder.API(
        title="Service", version="1.0", openapi="3.0.2", allowed_hosts=[";"]
    )

    @api.route("/search", methods=["GET"], params_model=SearchParams, response_model=Out)
    def search(req, resp):
        resp.media = {"results": []}

    dump = yaml.safe_load(api.requests.get("/schema.yml").content)
    params = {p["name"]: p for p in dump["paths"]["/search"]["parameters"]}
    assert params["q"]["in"] == "query"
    assert params["q"]["required"] is True
    assert params["q"]["schema"] == {"type": "string"}
    assert params["limit"]["required"] is False
    assert params["limit"]["schema"] == {"type": "integer", "default": 10}


# --- resp.render ---


def test_resp_render(tmp_path):
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "hello.html").write_text("<h1>Hello, {{ name }}!</h1>")

    api = responder.API(templates_dir=str(templates), allowed_hosts=[";"])

    @api.route("/")
    def home(req, resp):
        resp.render("hello.html", name="kenneth")

    r = api.requests.get("/")
    assert r.text == "<h1>Hello, kenneth!</h1>"
    assert "text/html" in r.headers["content-type"]


def test_resp_render_requires_api():
    from responder.formats import get_formats
    from responder.models import Request, Response

    scope = {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}

    async def receive():
        return {"type": "http.request", "body": b""}

    req = Request(scope, receive, formats=get_formats())
    resp = Response.__new__(Response)
    resp.req = req

    with pytest.raises(RuntimeError, match="bound to an API"):
        resp.render("x.html")
