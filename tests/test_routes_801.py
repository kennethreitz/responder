"""Regression tests for the 8.0.1 routing fixes."""

import asyncio
import time

import pytest

from responder.routes import Route

# --- Item 15: trailing-slash redirects ------------------------------------


def test_redirect_slashes_non_latin1_path(api):
    @api.route("/日本")
    def nihon(req, resp):
        resp.text = "ok"

    r = api.requests.get("/日本/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/%E6%97%A5%E6%9C%AC"

    followed = api.requests.get("/日本/")
    assert followed.status_code == 200
    assert followed.text == "ok"


def test_redirect_slashes_preserves_query_on_encoded_path(api):
    @api.route("/日本")
    def nihon(req, resp):
        resp.text = "ok"

    r = api.requests.get("/日本/?a=1", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/%E6%97%A5%E6%9C%AC?a=1"


def test_redirect_slashes_ignores_method(api):
    @api.route("/page", methods=["GET"])
    def page(req, resp):
        resp.text = "page"

    # A POST to the slashed variant must redirect just like a GET does;
    # the follow-up request is what earns the 405.
    r = api.requests.post("/page/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/page"

    r = api.requests.get("/page/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/page"


# --- Item 18: timeout response is detached from the abandoned view --------


def test_timeout_504_uses_fresh_response(api, monkeypatch):
    api.router.request_timeout = 0.05
    seen = {}
    original = Route._send_timeout_response

    async def spy(self, scope, receive, send, response):
        seen["timeout_response"] = response
        await original(self, scope, receive, send, response)

    monkeypatch.setattr(Route, "_send_timeout_response", spy)

    @api.route("/slow")
    def slow(req, resp):
        seen["view_response"] = resp
        time.sleep(0.3)
        # The zombie thread keeps mutating its Response after the 504.
        resp.status_code = 200
        resp.media = {"late": True}

    r = api.requests.get("/slow")
    assert r.status_code == 504
    # The 504 must not be built on the Response the abandoned view holds.
    assert seen["timeout_response"] is not seen["view_response"]
    # Let the zombie thread finish before the test tears down.
    time.sleep(0.35)


def test_timeout_504_keeps_hook_set_headers_and_cookies(api):
    """Metadata set by before_request hooks (which complete before the view
    starts) belongs on the 504 even though the Response object is rebuilt."""
    api.router.request_timeout = 0.05

    @api.before_request
    def add_request_id(req, resp):
        resp.headers["X-Request-ID"] = "abc123"
        resp.set_cookie("trace", "t1")

    @api.route("/slow")
    def slow(req, resp):
        time.sleep(0.3)
        resp.text = "done"

    r = api.requests.get("/slow")
    assert r.status_code == 504
    assert r.headers["x-request-id"] == "abc123"
    assert "trace=t1" in r.headers["set-cookie"]
    time.sleep(0.35)


def test_timeout_504_drops_stale_body_framing_headers(api):
    """Framing headers describing the abandoned body (e.g. resp.file()'s
    Content-Length) must not leak onto the 504 — they'd corrupt its framing."""
    api.router.request_timeout = 0.05

    @api.route("/slow")
    def slow(req, resp):
        resp.headers["Content-Length"] = "999999"
        resp.headers["Content-Type"] = "video/mp4"
        time.sleep(0.3)

    r = api.requests.get("/slow")
    assert r.status_code == 504
    assert r.headers["content-length"] == str(len(r.content))
    assert r.headers["content-type"] != "video/mp4"
    time.sleep(0.35)


# --- Item 19: route cache evicts instead of clearing -----------------------


def test_route_cache_keeps_hot_entries_under_param_churn(api):
    @api.route("/hot")
    def hot(req, resp):
        resp.text = "hot"

    @api.route("/users/{id}")
    def user(req, resp, *, id):
        resp.text = id

    router = api.router

    def resolve(path):
        scope = {"type": "http", "method": "GET", "path": path}
        assert router._resolve_route(scope) is not None

    resolve("/hot")
    for i in range(1023):
        resolve(f"/users/{i}")
    assert len(router._route_cache) == 1024

    # Touch the hot entry, then keep churning unique parameterized paths.
    resolve("/hot")
    for i in range(1023, 1033):
        resolve(f"/users/{i}")

    assert ("GET", "/hot") in router._route_cache
    assert len(router._route_cache) <= 1024


# --- Item 24: registration errors are real exceptions ----------------------


def test_route_without_leading_slash_raises_value_error(api):
    def view(req, resp):
        pass

    with pytest.raises(ValueError, match=r"start with '/'.*'users'"):
        api.add_route("users", view)


def test_websocket_route_without_leading_slash_raises_value_error(api):
    async def handler(ws):
        pass

    with pytest.raises(ValueError, match=r"start with '/'.*'chat'"):
        api.add_route("chat", handler, websocket=True)


def test_unknown_convertor_raises_value_error(api):
    def view(req, resp):
        pass

    with pytest.raises(ValueError, match=r"banana.*'/x/\{id:banana\}'"):
        api.add_route("/x/{id:banana}", view)


def test_duplicate_path_param_raises_value_error(api):
    def view(req, resp):
        pass

    with pytest.raises(ValueError, match=r"[Dd]uplicate.*'id'.*'/x/\{id\}/\{id\}'"):
        api.add_route("/x/{id}/{id}", view)


def test_add_event_handler_rejects_unknown_event(api):
    with pytest.raises(ValueError, match=r"startup.*shutdown.*'bogus'"):
        api.add_event_handler("bogus", lambda: None)


# --- Item 25: legacy shutdown failure still tears down app deps ------------


def test_legacy_shutdown_failure_runs_app_dependency_teardown(api):
    class StubDeps:
        def __init__(self):
            self.called = False

        async def shutdown(self):
            self.called = True

    stub = StubDeps()
    api.router.app_dependencies = stub

    @api.on_event("shutdown")
    def boom():
        raise RuntimeError("shutdown boom")

    async def drive():
        messages = []
        incoming = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]

        async def receive():
            return incoming.pop(0)

        async def send(message):
            messages.append(message)

        with pytest.raises(RuntimeError, match="shutdown boom"):
            await api.router({"type": "lifespan"}, receive, send)
        return messages

    messages = asyncio.run(drive())
    assert stub.called
    types = [m["type"] for m in messages]
    assert "lifespan.startup.complete" in types
    assert "lifespan.shutdown.failed" in types


def test_legacy_shutdown_success_still_completes(api):
    ran = []

    @api.on_event("shutdown")
    def fine():
        ran.append(True)

    async def drive():
        messages = []
        incoming = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]

        async def receive():
            return incoming.pop(0)

        async def send(message):
            messages.append(message)

        await api.router({"type": "lifespan"}, receive, send)
        return messages

    messages = asyncio.run(drive())
    assert ran == [True]
    assert messages[-1]["type"] == "lifespan.shutdown.complete"


# --- Item 10: class-based-view 405 carries an Allow header -----------------


def test_cbv_405_includes_allow_header(api):
    @api.route("/thing")
    class Thing:
        def on_get(self, req, resp):
            resp.text = "ok"

    r = api.requests.post("/thing")
    assert r.status_code == 405
    assert r.headers["allow"] == "GET, HEAD, OPTIONS"


def test_cbv_405_allow_lists_all_implemented_methods(api):
    @api.route("/multi")
    class Multi:
        def on_get(self, req, resp):
            resp.text = "g"

        def on_post(self, req, resp):
            resp.text = "p"

    r = api.requests.delete("/multi")
    assert r.status_code == 405
    assert set(r.headers["allow"].split(", ")) == {"GET", "HEAD", "OPTIONS", "POST"}


def test_cbv_without_get_has_no_implicit_head(api):
    @api.route("/postonly")
    class PostOnly:
        def on_post(self, req, resp):
            resp.text = "p"

    r = api.requests.get("/postonly")
    assert r.status_code == 405
    assert r.headers["allow"] == "OPTIONS, POST"


def test_cbv_allow_advertises_only_served_methods(api):
    """Every method the Allow header advertises must actually be served —
    including OPTIONS, which CBV dispatch (since 8.1) answers automatically
    with 200 + Allow when the class defines no on_options handler."""

    @api.route("/thing")
    class Thing:
        def on_get(self, req, resp):
            resp.text = "ok"

    r = api.requests.post("/thing")
    assert r.status_code == 405
    assert "OPTIONS" in r.headers["allow"].split(", ")
    # Every advertised method must actually be served.
    for method in r.headers["allow"].split(", "):
        assert api.requests.request(method, "/thing").status_code == 200


def test_cbv_405_allow_lists_explicit_on_options(api):
    @api.route("/opt")
    class Opt:
        def on_get(self, req, resp):
            resp.text = "g"

        def on_options(self, req, resp):
            resp.headers["Allow"] = "GET, HEAD, OPTIONS"

    r = api.requests.post("/opt")
    assert r.status_code == 405
    assert set(r.headers["allow"].split(", ")) == {"GET", "HEAD", "OPTIONS"}
    assert api.requests.options("/opt").status_code == 200


def test_api_head_and_options_shortcuts(api):
    @api.head("/probe")
    def probe_head(req, resp):
        resp.headers["X-Probe"] = "head"

    @api.options("/probe")
    def probe_options(req, resp):
        resp.text = "ok"

    assert api.requests.head("/probe").headers["x-probe"] == "head"
    assert api.requests.options("/probe").text == "ok"


def test_route_group_head_and_options_shortcuts(api):
    group = api.group("/v1")

    @group.head("/probe")
    def probe_head(req, resp):
        resp.headers["X-Probe"] = "head"

    @group.options("/probe")
    def probe_options(req, resp):
        resp.text = "ok"

    assert api.requests.head("/v1/probe").headers["x-probe"] == "head"
    assert api.requests.options("/v1/probe").text == "ok"
