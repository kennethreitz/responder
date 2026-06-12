"""Tests for dependency injection in route handlers."""

import pytest


def test_sync_function_dependency(api):
    @api.dependency()
    def greeting():
        return "hello"

    @api.route("/")
    def view(req, resp, *, greeting):
        resp.text = greeting

    r = api.requests.get("/")
    assert r.text == "hello"


def test_async_function_dependency(api):
    @api.dependency()
    async def number():
        return 42

    @api.route("/")
    async def view(req, resp, *, number):
        resp.media = {"number": number}

    r = api.requests.get("/")
    assert r.json() == {"number": 42}


def test_bare_decorator(api):
    @api.dependency
    def token():
        return "abc123"

    @api.route("/")
    def view(req, resp, *, token):
        resp.text = token

    r = api.requests.get("/")
    assert r.text == "abc123"


def test_explicit_name(api):
    @api.dependency(name="db")
    def make_database():
        return {"users": ["kenneth"]}

    @api.route("/")
    def view(req, resp, *, db):
        resp.media = db

    r = api.requests.get("/")
    assert r.json() == {"users": ["kenneth"]}


def test_add_dependency(api):
    api.add_dependency("config", lambda: {"debug": True})

    @api.route("/")
    def view(req, resp, *, config):
        resp.media = config

    r = api.requests.get("/")
    assert r.json() == {"debug": True}


def test_sync_generator_teardown(api):
    events = []

    @api.dependency()
    def resource():
        events.append("setup")
        yield "the-resource"
        events.append("teardown")

    @api.route("/")
    def view(req, resp, *, resource):
        events.append(f"handler:{resource}")
        resp.text = resource

    r = api.requests.get("/")
    assert r.text == "the-resource"
    assert events == ["setup", "handler:the-resource", "teardown"]


def test_async_generator_teardown(api):
    events = []

    @api.dependency()
    async def conn():
        events.append("setup")
        yield "connection"
        events.append("teardown")

    @api.route("/")
    async def view(req, resp, *, conn):
        events.append("handler")
        resp.text = conn

    r = api.requests.get("/")
    assert r.text == "connection"
    assert events == ["setup", "handler", "teardown"]


def test_teardown_runs_when_handler_raises(api):
    events = []

    @api.dependency()
    def resource():
        events.append("setup")
        yield "r"
        events.append("teardown")

    @api.route("/")
    def view(req, resp, *, resource):
        raise ValueError("boom")

    with pytest.raises(ValueError):
        api.requests.get("/")
    assert events == ["setup", "teardown"]


def test_provider_receives_request(api):
    @api.dependency()
    def user_agent(req):
        return req.headers.get("User-Agent", "unknown")

    @api.route("/")
    def view(req, resp, *, user_agent):
        resp.text = user_agent

    r = api.requests.get("/", headers={"User-Agent": "test-agent"})
    assert r.text == "test-agent"


def test_dependency_with_path_params(api):
    @api.dependency()
    def db():
        return {1: "kenneth", 2: "guido"}

    @api.route("/users/{id:int}")
    def view(req, resp, *, id, db):
        resp.text = db[id]

    r = api.requests.get("/users/2")
    assert r.text == "guido"


def test_path_param_shadows_dependency(api):
    @api.dependency(name="name")
    def name_dep():
        return "from-dependency"

    @api.route("/{name}")
    def view(req, resp, *, name):
        resp.text = name

    r = api.requests.get("/from-url")
    assert r.text == "from-url"


def test_resolved_once_per_request(api):
    instances = []

    @api.dependency()
    def tracker():
        obj = object()
        instances.append(obj)
        return obj

    @api.route("/")
    class Resource:
        def on_request(self, req, resp, *, tracker):
            resp.headers["X-Seen"] = "request"

        def on_get(self, req, resp, *, tracker):
            resp.text = "ok"

    r = api.requests.get("/")
    assert r.text == "ok"
    # Both views requested `tracker`, but the provider ran only once.
    assert len(instances) == 1


def test_class_based_view_injection(api):
    @api.dependency()
    def store():
        return ["a", "b"]

    @api.route("/items")
    class Items:
        def on_get(self, req, resp, *, store):
            resp.media = store

    r = api.requests.get("/items")
    assert r.json() == ["a", "b"]


def test_handler_without_dependencies_unaffected(api):
    @api.dependency()
    def unused():
        raise AssertionError("should not be resolved")

    @api.route("/")
    def view(req, resp):
        resp.text = "plain"

    r = api.requests.get("/")
    assert r.text == "plain"


# --- app-scoped dependencies ---


def test_app_scoped_dependency_resolved_once(api):
    calls = []

    @api.dependency(scope="app")
    def settings():
        calls.append(1)
        return {"env": "test"}

    @api.route("/")
    def view(req, resp, *, settings):
        resp.media = settings

    assert api.requests.get("/").json() == {"env": "test"}
    assert api.requests.get("/").json() == {"env": "test"}
    assert len(calls) == 1


def test_app_scoped_generator_teardown_at_shutdown(api):
    from starlette.testclient import TestClient as StarletteTestClient

    events = []

    @api.dependency(scope="app")
    async def pool():
        events.append("open")
        yield "the-pool"
        events.append("close")

    @api.route("/")
    async def view(req, resp, *, pool):
        resp.text = pool

    with StarletteTestClient(api, base_url="http://;") as client:
        assert client.get("/").text == "the-pool"
        assert client.get("/").text == "the-pool"
        assert events == ["open"]

    # Lifespan shutdown ran the teardown.
    assert events == ["open", "close"]


def test_app_scoped_provider_cannot_take_parameters(api):
    with pytest.raises(ValueError):

        @api.dependency(scope="app")
        def bad(req):
            return None


def test_invalid_scope_rejected(api):
    with pytest.raises(ValueError):
        api.add_dependency("x", lambda: 1, scope="session")
