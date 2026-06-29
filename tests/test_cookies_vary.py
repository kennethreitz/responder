"""v5.1: resp.delete_cookie(), resp.vary(), and opt-in auto Vary: Accept."""

from starlette.testclient import TestClient

import responder


def _client(api):
    return TestClient(api, base_url="http://;")


def _app(**kwargs):
    return responder.API(
        allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False, **kwargs
    )


def test_delete_cookie_expires_it():
    api = _app()

    @api.route("/logout")
    def logout(req, resp):
        resp.delete_cookie("token")
        resp.text = "bye"

    sc = _client(api).get("/logout").headers["set-cookie"]
    assert "token=" in sc
    assert "Max-Age=0" in sc


def test_delete_cookie_matches_path_and_domain():
    api = _app()

    @api.route("/logout")
    def logout(req, resp):
        resp.delete_cookie("token", path="/admin", domain="example.com")
        resp.text = "bye"

    sc = _client(api).get("/logout").headers["set-cookie"]
    assert "Path=/admin" in sc
    assert "Domain=example.com" in sc


def test_vary_merges_and_dedups():
    api = _app()

    @api.route("/v")
    def v(req, resp):
        resp.vary("Accept")
        resp.vary("accept", "Accept-Language")
        resp.media = {"ok": True}

    assert _client(api).get("/v").headers["vary"] == "Accept, Accept-Language"


def test_auto_vary_on_by_default():
    # v6: Vary: Accept is sent by default on negotiated responses.
    api = _app()

    @api.route("/j")
    def j(req, resp):
        resp.media = {"ok": True}

    assert _client(api).get("/j").headers["vary"] == "Accept"


def test_auto_vary_opt_out():
    api = _app(auto_vary=False)

    @api.route("/j")
    def j(req, resp):
        resp.media = {"ok": True}

    assert "vary" not in _client(api).get("/j").headers


def test_auto_vary_adds_accept_on_negotiated_response():
    api = _app(auto_vary=True)

    @api.route("/j")
    def j(req, resp):
        resp.media = {"ok": True}

    assert _client(api).get("/j").headers["vary"] == "Accept"


def test_auto_vary_not_added_to_non_negotiated_response():
    api = _app(auto_vary=True)

    @api.route("/t")
    def t(req, resp):
        resp.text = "hi"

    assert "vary" not in _client(api).get("/t").headers


def test_auto_vary_merges_with_explicit_vary():
    api = _app(auto_vary=True)

    @api.route("/j")
    def j(req, resp):
        resp.vary("Cookie")
        resp.media = {"ok": True}

    assert set(_client(api).get("/j").headers["vary"].split(", ")) == {"Accept", "Cookie"}
