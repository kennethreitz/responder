"""v5: deferred middleware stack + add_exception_handler."""

import pytest
from starlette.datastructures import MutableHeaders
from starlette.testclient import TestClient

import responder


@pytest.fixture
def make_api():
    def _make(**kwargs):
        kwargs.setdefault("allowed_hosts", [";"])
        return responder.API(**kwargs)

    return _make


def _no_raise_client(api):
    return TestClient(api, base_url="http://;", raise_server_exceptions=False)


class _HeaderMW:
    def __init__(self, app, name="X-Custom", value="yes"):
        self.app = app
        self.name = name
        self.value = value

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[self.name] = self.value
            await send(message)

        await self.app(scope, receive, send_wrapper)


class _BoomMW:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            raise RuntimeError("middleware boom")
        await self.app(scope, receive, send)


def test_add_middleware_after_construction(make_api):
    api = make_api()

    @api.route("/")
    def view(req, resp):
        resp.text = "ok"

    api.add_middleware(_HeaderMW)  # added AFTER __init__
    r = api.requests.get("/")
    assert r.headers.get("X-Custom") == "yes"


def test_add_exception_handler_programmatic(make_api):
    api = make_api()

    def handle(req, resp, exc):
        resp.status_code = 400
        resp.media = {"err": str(exc)}

    api.add_exception_handler(ValueError, handle)

    @api.route("/")
    def view(req, resp):
        raise ValueError("boom")

    r = api.requests.get("/", headers={"Accept": "application/json"})
    assert r.status_code == 400
    assert r.json() == {"err": "boom"}


def test_request_id_present_on_500(make_api):
    # The reconciliation: ServerErrorMiddleware renders the 500 INSIDE the
    # request-id tier, so 500s still carry X-Request-ID (v4.1 couldn't).
    api = make_api(request_id=True)

    @api.route("/boom")
    def boom(req, resp):
        raise RuntimeError("kaboom")

    r = _no_raise_client(api).get("/boom")
    assert r.status_code == 500
    assert "X-Request-ID" in r.headers


def test_user_middleware_errors_are_caught(make_api):
    # ServerErrorMiddleware is outermost over user middleware, so a crash in
    # user middleware renders a 500 instead of escaping as a raw ASGI error.
    api = make_api()

    @api.route("/")
    def view(req, resp):
        resp.text = "ok"

    api.add_middleware(_BoomMW)
    r = _no_raise_client(api).get("/")
    assert r.status_code == 500


def test_exception_handler_decorator_still_works(make_api):
    api = make_api()

    @api.exception_handler(KeyError)
    async def handle(req, resp, exc):
        resp.status_code = 422
        resp.text = "missing key"

    @api.route("/")
    def view(req, resp):
        raise KeyError("x")

    r = api.requests.get("/")
    assert r.status_code == 422
    assert r.text == "missing key"
