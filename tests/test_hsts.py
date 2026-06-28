"""v5.1: enable_hsts emits a real Strict-Transport-Security header."""

from starlette.testclient import TestClient

import responder
from responder.middleware import HSTSMiddleware

KEY = "a-real-private-secret-key-32chars!"


def _app(**kwargs):
    api = responder.API(allowed_hosts=[";"], secret_key=KEY, **kwargs)

    @api.route("/")
    def home(req, resp):
        resp.text = "ok"

    return api


def test_hsts_header_sent_when_enabled():
    api = _app(enable_hsts=True)
    r = TestClient(api, base_url="https://;").get("/")
    assert r.status_code == 200
    assert r.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


def test_hsts_still_redirects_http_to_https():
    api = _app(enable_hsts=True)
    r = TestClient(api, base_url="http://;").get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"].startswith("https://")


def test_no_hsts_header_by_default():
    api = _app()
    r = TestClient(api, base_url="https://;").get("/")
    assert "strict-transport-security" not in r.headers


def test_hsts_middleware_is_configurable():
    api = _app()
    api.add_middleware(HSTSMiddleware, max_age=63072000, preload=True)
    r = TestClient(api, base_url="https://;").get("/")
    assert r.headers["strict-transport-security"] == (
        "max-age=63072000; includeSubDomains; preload"
    )
