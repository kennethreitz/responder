"""v5: secure-by-default session configuration."""

import pytest
from starlette.testclient import TestClient

import responder

KEY = "a-real-private-secret-key-32chars!"


def _client(api):
    return TestClient(api, base_url="http://;")


def test_secure_flag_set_in_production():
    # debug=False + session_https_only default None => Secure cookie.
    api = responder.API(allowed_hosts=[";"], secret_key=KEY)

    @api.route("/", methods=["POST"])
    def view(req, resp):
        req.session["k"] = "v"
        resp.text = "ok"

    r = _client(api).post("/")
    assert "secure" in r.headers.get("set-cookie", "").lower()


def test_secure_flag_absent_when_disabled():
    api = responder.API(allowed_hosts=[";"], secret_key=KEY, session_https_only=False)

    @api.route("/", methods=["POST"])
    def view(req, resp):
        req.session["k"] = "v"
        resp.text = "ok"

    r = _client(api).post("/")
    assert "secure" not in r.headers.get("set-cookie", "").lower()


def test_samesite_none_without_secure_raises():
    with pytest.raises(ValueError, match="SameSite=None"):
        responder.API(
            allowed_hosts=[";"],
            secret_key=KEY,
            session_same_site="none",
            session_https_only=False,
        )


def test_sessions_true_without_key_raises(monkeypatch):
    monkeypatch.delenv("RESPONDER_SECRET_KEY", raising=False)
    from responder.ext.sessions import SessionConfigError

    with pytest.raises((SessionConfigError, ValueError)):
        responder.API(allowed_hosts=[";"], sessions=True)


def test_secret_key_from_env(monkeypatch):
    monkeypatch.setenv("RESPONDER_SECRET_KEY", KEY)
    api = responder.API(allowed_hosts=[";"], session_https_only=False)
    assert api.secret_key == KEY


def test_session_backend_with_sessions_false_raises():
    from responder.ext.sessions import MemorySessionBackend

    with pytest.raises(ValueError, match="sessions=False"):
        responder.API(
            allowed_hosts=[";"], sessions=False, session_backend=MemorySessionBackend()
        )
