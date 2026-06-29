"""v5.2: opt-in SecurityHeadersMiddleware."""

from starlette.testclient import TestClient

import responder
from responder.middleware import SecurityHeadersMiddleware


def _client(api):
    return TestClient(api, base_url="http://;")


def _app(**kwargs):
    api = responder.API(
        allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False, **kwargs
    )

    @api.route("/")
    def home(req, resp):
        resp.text = "ok"

    return api


def test_off_by_default():
    r = _client(_app()).get("/")
    assert "x-content-type-options" not in r.headers


def test_defaults_when_enabled():
    r = _client(_app(security_headers=True)).get("/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "content-security-policy" not in r.headers  # CSP is opt-in


def test_dict_config_adds_csp():
    api = _app(security_headers={"content_security_policy": "default-src 'self'"})
    r = _client(api).get("/")
    assert r.headers["content-security-policy"] == "default-src 'self'"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_handler_set_header_is_preserved():
    api = responder.API(
        allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False,
        security_headers=True,
    )

    @api.route("/")
    def home(req, resp):
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.text = "ok"

    assert _client(api).get("/").headers["x-frame-options"] == "SAMEORIGIN"


def test_installable_via_add_middleware():
    api = _app()
    api.add_middleware(SecurityHeadersMiddleware, headers={"X-Test": "1"})
    r = _client(api).get("/")
    assert r.headers["x-test"] == "1"
    assert r.headers["x-content-type-options"] == "nosniff"
