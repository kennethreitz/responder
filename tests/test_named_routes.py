"""v5.4: named routes for url_for reverse lookup."""

import responder


def _api():
    return responder.API(allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False)


def test_url_for_by_name():
    api = _api()

    @api.route("/users/{id}", name="user_detail")
    def user_detail(req, resp, *, id):
        resp.media = {"id": id}

    assert api.url_for("user_detail", id=42) == "/users/42"


def test_url_for_by_function_still_works():
    api = _api()

    @api.route("/p/{slug}")
    def page(req, resp, *, slug):
        resp.text = slug

    assert api.url_for(page, slug="x") == "/p/x"
    assert api.url_for("page", slug="x") == "/p/x"


def test_name_disambiguates_lambdas():
    api = _api()

    # Two routes whose endpoints share the generic name "<lambda>".
    api.add_route("/a", lambda req, resp: None, name="alpha", static=False)
    api.add_route("/b", lambda req, resp: None, name="beta", static=False)

    assert api.url_for("alpha") == "/a"
    assert api.url_for("beta") == "/b"


def test_named_websocket_route():
    api = _api()

    @api.websocket_route("/ws", name="socket")
    async def ws(ws):
        ...

    assert api.url_for("socket") == "/ws"


def test_unknown_name_raises():
    import pytest

    from responder.routes import RouteNotFoundError

    with pytest.raises(RouteNotFoundError):
        _api().url_for("nope")


def test_verb_decorator_accepts_name():
    api = _api()

    @api.get("/g", name="g_route")
    def g(req, resp):
        resp.text = "g"

    assert api.url_for("g_route") == "/g"
