"""Regression tests for the 8.0.1 plumbing fixes."""

import logging
import pickle
import signal
import socket
import sys
import textwrap
import threading
import types

import pytest

import responder
from responder import Query
from responder.formats import _parse_multipart
from responder.middleware import SecurityHeadersMiddleware
from responder.util.cmd import ResponderServer
from responder.util.python import _load_target_basic

# ---------------------------------------------------------------------------
# [43] Optional sequence Query params (list[int] | None) must bind repeats.
# ---------------------------------------------------------------------------


def test_optional_sequence_query_binds_repeats(api):
    @api.route("/tags")
    async def tags_view(req, resp, *, tags: list[int] | None = Query(None)):
        resp.media = {"tags": tags}

    r = api.requests.get("http://;/tags?tags=1&tags=2")
    assert r.status_code == 200
    assert r.json() == {"tags": [1, 2]}


def test_optional_sequence_query_absent_uses_default(api):
    @api.route("/tags")
    async def tags_view(req, resp, *, tags: list[int] | None = Query(None)):
        resp.media = {"tags": tags}

    r = api.requests.get("http://;/tags")
    assert r.status_code == 200
    assert r.json() == {"tags": None}


def test_optional_sequence_query_typing_optional(api):
    from typing import Optional  # noqa: UP035 - explicitly testing Optional[...]

    @api.route("/items")
    async def items_view(
        req,
        resp,
        *,
        items: Optional[list[str]] = Query(None),  # noqa: UP045
    ):
        resp.media = {"items": items}

    r = api.requests.get("http://;/items?items=a&items=b")
    assert r.status_code == 200
    assert r.json() == {"items": ["a", "b"]}


def test_plain_optional_scalar_query_still_works(api):
    @api.route("/one")
    async def one_view(req, resp, *, q: int | None = Query(None)):
        resp.media = {"q": q}

    assert api.requests.get("http://;/one?q=5").json() == {"q": 5}
    assert api.requests.get("http://;/one").json() == {"q": None}


# ---------------------------------------------------------------------------
# [49] Async problem_handler must be awaited on the per-request error path.
# ---------------------------------------------------------------------------


def test_async_problem_handler_is_awaited_on_404():
    api = responder.API(allowed_hosts=[";"])

    async def enrich(payload, request, exc):
        payload["enriched"] = "async"

    api.problem_handler = enrich

    r = api.requests.get("http://;/definitely-not-a-route")
    assert r.status_code == 404
    assert r.json()["enriched"] == "async"


def test_async_problem_handler_replacement_payload():
    api = responder.API(allowed_hosts=[";"])

    async def replace(payload, request, exc):
        return {**payload, "replaced": True}

    api.problem_handler = replace

    r = api.requests.get("http://;/nope")
    assert r.status_code == 404
    assert r.json()["replaced"] is True


def test_sync_problem_handler_still_works():
    def enrich(payload, request, exc):
        payload["enriched"] = "sync"

    api = responder.API(allowed_hosts=[";"], problem_handler=enrich)

    r = api.requests.get("http://;/nope")
    assert r.status_code == 404
    assert r.json()["enriched"] == "sync"


def test_async_problem_handler_applied_via_resp_problem():
    """resp.problem() is a synchronous call site; an async handler is run to
    completion on a private event loop instead of being skipped."""
    api = responder.API(allowed_hosts=[";"])

    async def enrich(payload, request, exc):
        payload["enriched"] = True

    api.problem_handler = enrich

    @api.route("/conflict")
    async def conflict(req, resp):
        resp.problem(409, detail="conflict")

    r = api.requests.get("http://;/conflict")
    assert r.status_code == 409
    payload = r.json()
    assert payload["detail"] == "conflict"
    assert payload["enriched"] is True


def test_async_problem_handler_awaited_on_route_validation_error(caplog):
    """Route-level errors (e.g. 422) are built on a synchronous path; an
    async handler must still be applied, without per-request warning spam."""
    api = responder.API(allowed_hosts=[";"])

    async def enrich(payload, request, exc):
        payload["enriched"] = "async"

    api.problem_handler = enrich

    @api.route("/typed")
    async def typed(req, resp, *, n: int = Query(...)):
        resp.media = {"n": n}

    with caplog.at_level(logging.WARNING, logger="responder.errors"):
        r = api.requests.get("http://;/typed?n=notanint")
    assert r.status_code == 422
    assert r.json()["enriched"] == "async"
    assert not [
        rec for rec in caplog.records if "problem_handler" in rec.getMessage()
    ]


# ---------------------------------------------------------------------------
# [50] SecurityHeadersMiddleware: None header value means "omit this header".
# ---------------------------------------------------------------------------


def test_security_headers_none_drops_default(api):
    api.add_middleware(
        SecurityHeadersMiddleware, headers={"x-frame-options": None}
    )

    @api.route("/")
    async def home(req, resp):
        resp.text = "hello"

    r = api.requests.get("http://;/")
    assert r.status_code == 200
    assert "x-frame-options" not in r.headers
    assert r.headers["x-content-type-options"] == "nosniff"


def test_security_headers_override_still_works(api):
    api.add_middleware(
        SecurityHeadersMiddleware, headers={"x-frame-options": "SAMEORIGIN"}
    )

    @api.route("/")
    async def home(req, resp):
        resp.text = "hello"

    r = api.requests.get("http://;/")
    assert r.headers["x-frame-options"] == "SAMEORIGIN"


# ---------------------------------------------------------------------------
# [54] _load_target_basic must register the module in sys.modules.
# ---------------------------------------------------------------------------

_APP_SOURCE = textwrap.dedent(
    """
    from dataclasses import dataclass

    import responder

    api = responder.API()


    @dataclass
    class Point:
        x: int
        y: int
    """
)


def test_load_target_basic_registers_sys_modules(tmp_path):
    app_file = tmp_path / "plumbing801app.py"
    app_file.write_text(_APP_SOURCE)
    try:
        api = _load_target_basic(str(app_file), "api")
        assert isinstance(api, responder.API)
        assert "plumbing801app" in sys.modules
        # Pickling a module-level class requires the module to be findable
        # by name in sys.modules.
        point = sys.modules["plumbing801app"].Point(1, 2)
        assert pickle.loads(pickle.dumps(point)) == point  # noqa: S301 - own data
    finally:
        sys.modules.pop("plumbing801app", None)


def test_load_target_basic_unregisters_on_exec_failure(tmp_path):
    app_file = tmp_path / "plumbing801broken.py"
    app_file.write_text("raise RuntimeError('boom')\n")
    with pytest.raises(RuntimeError, match="boom"):
        _load_target_basic(str(app_file), "api")
    assert "plumbing801broken" not in sys.modules


def test_load_target_basic_does_not_clobber_existing_module(tmp_path):
    """A file target whose stem collides with an imported module must not
    replace that module in sys.modules; it registers under its own name."""
    app_file = tmp_path / "plumbing801taken.py"
    app_file.write_text(_APP_SOURCE)
    sentinel = types.ModuleType("plumbing801taken")
    sys.modules["plumbing801taken"] = sentinel
    added = None
    try:
        point_cls = _load_target_basic(f"{app_file}:Point", "api")
        added = point_cls.__module__
        assert sys.modules["plumbing801taken"] is sentinel
        assert added != "plumbing801taken"
        assert sys.modules[added].Point is point_cls
        # __name__ matches the sys.modules key, so pickle still works.
        point = point_cls(1, 2)
        assert pickle.loads(pickle.dumps(point)) == point  # noqa: S301 - own data
    finally:
        sys.modules.pop("plumbing801taken", None)
        if added is not None:
            sys.modules.pop(added, None)


def test_load_target_basic_failure_keeps_existing_module(tmp_path):
    """Exec failure removes only the entry the loader added — never a
    pre-existing module that happened to share the file's stem."""
    app_file = tmp_path / "plumbing801keep.py"
    app_file.write_text("raise RuntimeError('boom')\n")
    sentinel = types.ModuleType("plumbing801keep")
    sys.modules["plumbing801keep"] = sentinel
    try:
        with pytest.raises(RuntimeError, match="boom"):
            _load_target_basic(str(app_file), "api")
        assert sys.modules["plumbing801keep"] is sentinel
        assert "_responder_target_plumbing801keep" not in sys.modules
    finally:
        sys.modules.pop("plumbing801keep", None)


# ---------------------------------------------------------------------------
# [55] Multipart part bodies accumulate in a bytearray, exposed as bytes.
# ---------------------------------------------------------------------------


def test_multipart_part_body_is_bytes():
    body = (
        b"--boundary\r\n"
        b'Content-Disposition: form-data; name="field"\r\n'
        b"\r\n"
        b"hello world\r\n"
        b"--boundary--\r\n"
    )
    parts = _parse_multipart(body, "multipart/form-data; boundary=boundary")
    assert len(parts) == 1
    assert parts[0].body == b"hello world"
    assert isinstance(parts[0].body, bytes)


def test_multipart_form_round_trip(api):
    @api.route("/form", methods=["POST"])
    async def form_view(req, resp):
        data = await req.media("form")
        resp.media = {"field": data["field"]}

    r = api.requests.post("http://;/form", files={"field": (None, "hello")})
    assert r.status_code == 200
    assert r.json() == {"field": "hello"}


# ---------------------------------------------------------------------------
# [46] ResponderServer: off-main-thread construction, pre-start safety,
#      and signal-handler hygiene.
# ---------------------------------------------------------------------------


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def test_responder_server_constructs_off_main_thread():
    results = {}

    def construct():
        try:
            results["server"] = ResponderServer("app.py", port=_free_port())
        except Exception as exc:  # pragma: no cover - failure path
            results["error"] = exc

    t = threading.Thread(target=construct)
    t.start()
    t.join()
    assert "error" not in results, results.get("error")


def test_responder_server_safe_before_start():
    server = ResponderServer("app.py", port=_free_port())
    assert server.is_running() is False
    assert server.wait_until_ready(timeout=1) is False
    server.stop()  # must not raise


def test_responder_server_init_does_not_clobber_signal_handlers():
    before_term = signal.getsignal(signal.SIGTERM)
    before_int = signal.getsignal(signal.SIGINT)
    ResponderServer("app.py", port=_free_port())
    assert signal.getsignal(signal.SIGTERM) is before_term
    assert signal.getsignal(signal.SIGINT) is before_int


def test_responder_server_signal_handlers_install_and_restore():
    before_term = signal.getsignal(signal.SIGTERM)
    before_int = signal.getsignal(signal.SIGINT)
    server = ResponderServer("app.py", port=_free_port())
    server._install_signal_handlers()
    try:
        assert signal.getsignal(signal.SIGTERM) == server._signal_handler
        assert signal.getsignal(signal.SIGINT) == server._signal_handler
    finally:
        server._restore_signal_handlers()
    assert signal.getsignal(signal.SIGTERM) is before_term
    assert signal.getsignal(signal.SIGINT) is before_int


def test_responder_server_non_lifo_stop_restores_original_handlers():
    """Stopping servers out of start order must not resurrect a stopped
    server's (signal-swallowing) handler; the originals come back."""
    before_term = signal.getsignal(signal.SIGTERM)
    before_int = signal.getsignal(signal.SIGINT)
    server_a = ResponderServer("app.py", port=_free_port())
    server_b = ResponderServer("app.py", port=_free_port())
    try:
        server_a._install_signal_handlers()
        server_b._install_signal_handlers()

        server_a.stop()
        # B is still running: its handler must stay installed.
        assert signal.getsignal(signal.SIGTERM) == server_b._signal_handler
        assert signal.getsignal(signal.SIGINT) == server_b._signal_handler

        server_b.stop()
        # The originals return — not stopped A's dead handler.
        assert signal.getsignal(signal.SIGTERM) is before_term
        assert signal.getsignal(signal.SIGINT) is before_int
    finally:
        signal.signal(signal.SIGTERM, before_term)
        signal.signal(signal.SIGINT, before_int)


def test_responder_server_lifo_stop_keeps_live_server_handler():
    """LIFO stops keep working: stopping B restores A's (still live) handler,
    then stopping A restores the originals."""
    before_term = signal.getsignal(signal.SIGTERM)
    before_int = signal.getsignal(signal.SIGINT)
    server_a = ResponderServer("app.py", port=_free_port())
    server_b = ResponderServer("app.py", port=_free_port())
    try:
        server_a._install_signal_handlers()
        server_b._install_signal_handlers()

        server_b.stop()
        assert signal.getsignal(signal.SIGTERM) == server_a._signal_handler

        server_a.stop()
        assert signal.getsignal(signal.SIGTERM) is before_term
        assert signal.getsignal(signal.SIGINT) is before_int
    finally:
        signal.signal(signal.SIGTERM, before_term)
        signal.signal(signal.SIGINT, before_int)


# ---------------------------------------------------------------------------
# [6] path_matches_route accepts the documented plain path string.
# ---------------------------------------------------------------------------


def test_path_matches_route_accepts_path_string(api):
    @api.route("/hello")
    def hello(req, resp):
        resp.text = "hi"

    route = api.path_matches_route("/hello")
    assert route is not None
    assert route.route == "/hello"
    assert api.path_matches_route("/nope") is None


def test_path_matches_route_matches_method_restricted_routes(api):
    @api.route("/posty", methods=["POST"])
    def posty(req, resp):
        resp.text = "posted"

    route = api.path_matches_route("/posty")
    assert route is not None
    assert route.route == "/posty"


def test_path_matches_route_matches_websocket_routes(api):
    @api.route("/ws-path", websocket=True)
    async def ws_handler(ws):
        pass  # pragma: no cover

    route = api.path_matches_route("/ws-path")
    assert route is not None
    assert route.route == "/ws-path"


def test_path_matches_route_still_accepts_scope_mapping(api):
    @api.route("/hello")
    def hello(req, resp):
        resp.text = "hi"

    scope = {"type": "http", "path": "/hello"}
    route = api.path_matches_route(scope)
    assert route is not None
    assert route.route == "/hello"


# ---------------------------------------------------------------------------
# [9] session(base_url) rebuilds the cached client when base_url differs.
# ---------------------------------------------------------------------------


def test_session_rebuilds_on_different_base_url():
    api = responder.API(allowed_hosts=["*"])

    # v8.1: session() is deprecated (use api.requests); it warns per call
    # but keeps its per-base_url caching semantics until 9.0.
    with pytest.warns(DeprecationWarning, match=r"api\.requests"):
        first = api.session()
    assert str(first.base_url).startswith("http://;")

    with pytest.warns(DeprecationWarning):
        localhost = api.session("http://localhost")
    assert str(localhost.base_url).startswith("http://localhost")

    # Same base_url returns the cached client.
    with pytest.warns(DeprecationWarning):
        assert api.session("http://localhost") is localhost

    # Switching back rebuilds again with the default.
    with pytest.warns(DeprecationWarning):
        again = api.session()
    assert str(again.base_url).startswith("http://;")
