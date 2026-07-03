"""Tests for the ``@api.middleware("http")`` function-middleware decorator."""

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from starlette.testclient import TestClient

import responder
from responder.middleware import FunctionMiddleware


def _api(**kwargs):
    return responder.API(
        secret_key="x" * 32,
        allowed_hosts=[";"],
        session_https_only=False,
        **kwargs,
    )


def _client(api):
    return TestClient(api, base_url="http://;", raise_server_exceptions=False)


def test_async_function_middleware_adds_header():
    api = _api()

    @api.middleware("http")
    async def add_timing(request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Response-Time"] = f"{time.perf_counter() - start:.4f}s"
        return response

    @api.route("/")
    def index(req, resp):
        resp.media = {"ok": True}

    r = _client(api).get("/")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert float(r.headers["x-response-time"].rstrip("s")) >= 0


def test_sync_function_middleware_offloaded_with_blocking_call_next():
    api = _api()
    seen = {}

    @api.middleware("http")
    def tag_response(request, call_next):
        seen["thread"] = threading.current_thread().name
        response = call_next(request)
        response.headers["X-Tag"] = "sync"
        return response

    @api.route("/")
    async def index(req, resp):
        resp.media = {"ok": True}

    r = _client(api).get("/")
    assert r.status_code == 200
    assert r.headers["x-tag"] == "sync"
    # The sync middleware ran off the event loop, like sync views do.
    assert "thread" in seen
    assert seen["thread"] != "MainThread"


def test_two_sync_middlewares_stack():
    api = _api()
    order = []

    @api.middleware("http")
    def inner(request, call_next):
        order.append("inner:in")
        response = call_next(request)
        order.append("inner:out")
        return response

    @api.middleware("http")
    def outer(request, call_next):
        order.append("outer:in")
        response = call_next(request)
        order.append("outer:out")
        return response

    @api.route("/")
    def index(req, resp):
        resp.media = {"ok": True}

    r = _client(api).get("/")
    assert r.status_code == 200
    assert order == ["outer:in", "inner:in", "inner:out", "outer:out"]


def test_middleware_can_short_circuit():
    from starlette.responses import JSONResponse

    api = _api()

    @api.middleware("http")
    async def block(request, call_next):
        if request.url.path == "/blocked":
            return JSONResponse({"blocked": True}, status_code=403)
        return await call_next(request)

    @api.route("/blocked")
    def blocked(req, resp):
        resp.media = {"ok": True}

    @api.route("/open")
    def open_route(req, resp):
        resp.media = {"ok": True}

    client = _client(api)
    r = client.get("/blocked")
    assert r.status_code == 403
    assert r.json() == {"blocked": True}
    assert client.get("/open").json() == {"ok": True}


def test_middleware_order_last_registered_is_outermost():
    api = _api()
    order = []

    @api.middleware("http")
    async def first(request, call_next):
        order.append("first:in")
        response = await call_next(request)
        order.append("first:out")
        return response

    @api.middleware("http")
    async def second(request, call_next):
        order.append("second:in")
        response = await call_next(request)
        order.append("second:out")
        return response

    @api.route("/")
    def index(req, resp):
        resp.media = {"ok": True}

    assert _client(api).get("/").status_code == 200
    # Most-recently-registered runs first (outermost), matching add_middleware.
    assert order == ["second:in", "first:in", "first:out", "second:out"]


def test_middleware_composes_with_add_middleware():
    api = _api()
    order = []

    @api.middleware("http")
    async def func_mw(request, call_next):
        order.append("func:in")
        response = await call_next(request)
        order.append("func:out")
        return response

    class ClassMW:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            order.append("class:in")
            await self.app(scope, receive, send)
            order.append("class:out")

    api.add_middleware(ClassMW)

    @api.route("/")
    def index(req, resp):
        resp.media = {"ok": True}

    assert _client(api).get("/").status_code == 200
    # The class middleware was added last, so it is outermost.
    assert order == ["class:in", "func:in", "func:out", "class:out"]


def test_bare_decorator_usage():
    api = _api()

    @api.middleware
    async def stamp(request, call_next):
        response = await call_next(request)
        response.headers["X-Stamped"] = "yes"
        return response

    @api.route("/")
    def index(req, resp):
        resp.media = {"ok": True}

    assert _client(api).get("/").headers["x-stamped"] == "yes"


def test_decorator_returns_the_function_unwrapped():
    api = _api()

    async def mw(request, call_next):
        return await call_next(request)

    assert api.middleware("http")(mw) is mw


def test_non_http_middleware_type_rejected():
    api = _api()
    with pytest.raises(ValueError, match="http"):
        api.middleware("websocket")


def test_middleware_sees_error_responses():
    api = _api()

    @api.middleware("http")
    async def stamp(request, call_next):
        response = await call_next(request)
        response.headers["X-Stamped"] = "yes"
        return response

    @api.route("/")
    def index(req, resp):
        resp.media = {"ok": True}

    r = _client(api).get("/missing")
    assert r.status_code == 404
    # User middleware sits above ExceptionMiddleware, so rendered
    # errors flow back through it.
    assert r.headers["x-stamped"] == "yes"


def test_websocket_traffic_passes_through():
    api = _api()

    @api.middleware("http")
    async def stamp(request, call_next):
        return await call_next(request)

    @api.route("/ws", websocket=True)
    async def ws(ws):
        await ws.accept()
        await ws.send_text("hello")
        await ws.close()

    with _client(api).websocket_connect("ws://;/ws") as session:
        assert session.receive_text() == "hello"


def test_function_middleware_installable_directly():
    api = _api()

    async def stamp(request, call_next):
        response = await call_next(request)
        response.headers["X-Direct"] = "yes"
        return response

    api.add_middleware(FunctionMiddleware, func=stamp)

    @api.route("/")
    def index(req, resp):
        resp.media = {"ok": True}

    assert _client(api).get("/").headers["x-direct"] == "yes"


def test_middleware_registered_after_first_request():
    api = _api()

    @api.route("/")
    def index(req, resp):
        resp.media = {"ok": True}

    client = _client(api)
    assert "x-late" not in client.get("/").headers

    @api.middleware("http")
    async def late(request, call_next):
        response = await call_next(request)
        response.headers["X-Late"] = "yes"
        return response

    # The stack rebuilds lazily, so a new client sees the middleware.
    assert _client(api).get("/").headers["x-late"] == "yes"


def _live_api(**kwargs):
    # A live server receives a real ``Host: 127.0.0.1:<port>`` header, so the
    # host allowlist must accept it (unlike the TestClient-based ``_api``).
    return responder.API(
        secret_key="x" * 32,
        allowed_hosts=["*"],
        session_https_only=False,
        **kwargs,
    )


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _LiveServer:
    """Run an app under a real uvicorn server on a background thread.

    A live server is required to exercise the deadlock: a sync
    ``@api.middleware("http")`` runs on an anyio worker thread and then blocks
    it waiting for the (async) downstream call, while the downstream sync view
    needs a *second* worker thread. Both come from anyio's shared threadpool
    limiter (40 tokens by default), so enough concurrent requests would exhaust
    it and hang the whole server. TestClient serializes requests through a
    single portal, so it cannot surface this — we need real concurrent sockets.
    """

    def __init__(self, api: responder.API) -> None:
        self.api = api
        self.port = _free_port()

    def __enter__(self) -> "_LiveServer":
        import uvicorn

        config = uvicorn.Config(
            self.api, host="127.0.0.1", port=self.port, log_level="error"
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        # Wait for the server to start accepting connections.
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), 0.2):
                    return self
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("live server did not start in time")

    def __exit__(self, *exc: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/"


def _hammer(server, n, timeout=20.0):
    """Fire ``n`` concurrent GETs; return the list of status codes.

    Uses one thread per request against a live socket so the middleware's
    blocking ``call_next`` genuinely competes with downstream sync work for
    threadpool tokens.
    """
    import httpx

    limits = httpx.Limits(max_connections=n + 10, max_keepalive_connections=n + 10)
    results = []
    with httpx.Client(limits=limits, timeout=timeout / 2) as client:

        def one(_):
            return client.get(server.url).status_code

        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(one, i) for i in range(n)]
            deadline = time.time() + timeout
            for fut in as_completed(futures, timeout=timeout):
                results.append(fut.result())
                assert time.time() < deadline, "requests did not finish in time"
    return results


pytest.importorskip("uvicorn")
pytest.importorskip("httpx")


# anyio's shared threadpool limiter defaults to 40 tokens. We deliberately
# park MORE than that many sync-middleware bodies simultaneously (via a
# barrier) so the buggy version — which borrows a shared token per middleware
# body — can never assemble enough parked bodies to release the barrier and
# deadlocks. The fixed version runs bodies on a separate unbounded limiter, so
# all of them park, the barrier releases, and downstream sync views then draw
# from the (untouched) shared pool.
_PARKED = 45


def test_sync_middleware_no_threadpool_deadlock_under_concurrency():
    """Regression: a sync middleware that blocks on ``call_next`` plus a sync
    view must not exhaust anyio's shared threadpool under load.

    Before the fix, the sync middleware borrowed a token from the shared
    limiter (40) and held it while blocking on the downstream sync view, which
    needed a second token — so >=40 concurrent requests deadlocked forever.
    """
    api = _live_api()
    barrier = threading.Barrier(_PARKED, timeout=15)

    @api.middleware("http")
    def sync_mw(request, call_next):
        # Hold this worker thread until _PARKED bodies are all parked here at
        # once. Under the bug only 40 can run concurrently, so the barrier
        # (45) never trips and the server hangs.
        barrier.wait()
        response = call_next(request)
        response.headers["X-Sync"] = "1"
        return response

    @api.route("/")
    def index(req, resp):  # sync view -> needs its own downstream token
        resp.media = {"ok": True}

    with _LiveServer(api) as server:
        codes = _hammer(server, _PARKED)

    assert len(codes) == _PARKED
    assert all(code == 200 for code in codes)


def test_two_stacked_sync_middlewares_no_deadlock_under_concurrency():
    """Two stacked sync middlewares each blocking on ``call_next`` (plus a sync
    view) previously deadlocked at roughly half the token budget."""
    api = _live_api()
    barrier = threading.Barrier(_PARKED, timeout=15)

    @api.middleware("http")
    def inner(request, call_next):
        return call_next(request)

    @api.middleware("http")
    def outer(request, call_next):
        # Parking in the outermost body pins one worker thread per request;
        # the buggy version cannot gather _PARKED of them from the 40-token
        # shared pool.
        barrier.wait()
        return call_next(request)

    @api.route("/")
    def index(req, resp):
        resp.media = {"ok": True}

    with _LiveServer(api) as server:
        codes = _hammer(server, _PARKED)

    assert len(codes) == _PARKED
    assert all(code == 200 for code in codes)
