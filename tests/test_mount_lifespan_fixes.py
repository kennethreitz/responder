"""Mount, lifespan, and auto-etag fixes:

- a user lifespan context manager receives the API as ``app`` (was None)
- WSGI-vs-ASGI mount dispatch is decided by signature, so a genuine TypeError
  inside a mounted ASGI sub-app keeps its real traceback
- auto_etag still tags large bodies (hashing offloaded off the event loop)
"""

from contextlib import asynccontextmanager

import pytest
from starlette.testclient import TestClient

import responder


def test_lifespan_receives_the_api_as_app():
    seen = {}

    @asynccontextmanager
    async def lifespan(app):
        seen["app"] = app
        yield

    api = responder.API(lifespan=lifespan, allowed_hosts=[";"])

    @api.route("/")
    def index(req, resp):
        resp.text = "ok"

    with api.requests as session:
        session.get("http://;/")

    assert seen["app"] is api


def test_mounted_asgi_app_real_error_is_not_masked():
    api = responder.API(allowed_hosts=["localhost"])

    async def broken_asgi(scope, receive, send):
        # A genuine bug inside the ASGI app — NOT a call-signature error.
        raise TypeError("boom: a real bug with the word argument in it")

    api.mount("/sub", broken_asgi)

    @api.route("/")
    def index(req, resp):
        resp.text = "root"

    client = TestClient(api, base_url="http://localhost", raise_server_exceptions=True)
    # The real TypeError must propagate, not be swallowed by a WSGI fallback.
    with pytest.raises(TypeError, match="boom"):
        client.get("/sub")


def test_mounted_asgi_app_still_works():
    api = responder.API(allowed_hosts=["localhost"])

    async def asgi_ok(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"asgi-ok"})

    api.mount("/sub", asgi_ok)
    r = TestClient(api, base_url="http://localhost").get("/sub")
    assert r.status_code == 200
    assert r.text == "asgi-ok"


def test_mounted_wsgi_app_still_works():
    flask = pytest.importorskip("flask")
    wsgi = flask.Flask(__name__)

    @wsgi.route("/")
    def home():
        return "wsgi-ok"

    api = responder.API(allowed_hosts=["localhost"])
    api.mount("/wsgi", wsgi)
    r = TestClient(api, base_url="http://localhost").get("/wsgi/")
    assert r.status_code == 200
    assert "wsgi-ok" in r.text


def test_auto_etag_large_body_is_tagged():
    api = responder.API(allowed_hosts=[";"], auto_etag=True, session_https_only=False)

    @api.route("/big")
    def big(req, resp):
        resp.text = "x" * 200_000  # exceeds the off-loop hashing threshold

    r = TestClient(api, base_url="http://;").get("/big")
    assert r.status_code == 200
    assert "ETag" in r.headers
    # And the ETag actually validates (conditional 304).
    r2 = TestClient(api, base_url="http://;").get(
        "/big", headers={"If-None-Match": r.headers["ETag"]}
    )
    assert r2.status_code == 304
