Testing
=======

Responder includes a built-in test client powered by Starlette's
``TestClient``. You don't need to start a server — tests run in-process,
making them fast and reliable. There's no separate test server to manage,
no ports to allocate, and no race conditions to worry about. Just import
your app and start making requests.


Getting Started
---------------

Given a simple application in ``api.py``::

    import responder

    api = responder.API()

    @api.route("/")
    def hello(req, resp):
        resp.text = "hello, world!"

    if __name__ == "__main__":
        api.run()

You can test it with pytest. Every Responder ``API`` instance has a
``requests`` property that gives you a test client — use it exactly like
you'd use ``requests`` or ``httpx``::

    # test_api.py
    import api as service

    def test_hello():
        r = service.api.requests.get("/")
        assert r.text == "hello, world!"

Run your tests::

    $ pytest

That's really all there is to it. No configuration, no test server setup.


Using Fixtures
--------------

For larger test suites, pytest fixtures keep things organized. Create a
fixture that returns your API instance, and every test gets a fresh
reference to it::

    import pytest
    import api as service

    @pytest.fixture
    def api():
        return service.api

    def test_hello(api):
        r = api.requests.get("/")
        assert r.text == "hello, world!"

    def test_json(api):
        @api.route("/data")
        def data(req, resp):
            resp.media = {"key": "value"}

        r = api.requests.get(api.url_for(data))
        assert r.json() == {"key": "value"}

The ``api.url_for()`` method generates a URL for a given route endpoint,
so you don't have to hard-code paths in your tests. If you rename a route
later, your tests won't break.


Testing JSON APIs
-----------------

Most APIs send and receive JSON. The test client makes this natural — pass
``json=`` to send a JSON body, and call ``.json()`` on the response to
parse it::

    def test_create_item(api):
        @api.route("/items")
        async def create(req, resp):
            data = await req.media()
            resp.media = {"created": data}
            resp.status_code = 201

        r = api.requests.post(api.url_for(create), json={"name": "widget"})
        assert r.status_code == 201
        assert r.json() == {"created": {"name": "widget"}}

You can also test content negotiation by setting the ``Accept`` header::

    r = api.requests.get("/data", headers={"Accept": "application/x-yaml"})
    assert "key: value" in r.text


Testing Request Validation
--------------------------

Responder validates typed handler inputs for you, returning ``422`` with a
``{"errors": [...]}`` body when something doesn't fit. That makes validation
tests delightfully boring: send bad data, assert the status code.

The simplest form is a Pydantic-annotated body parameter. On ``POST``,
``PUT``, ``PATCH``, and ``DELETE`` it receives the parsed, validated body —
no ``await req.media()`` required::

    from pydantic import BaseModel

    class Item(BaseModel):
        name: str
        price: float

    def test_body_validation(api):
        @api.route("/items", methods=["POST"])
        async def create(req, resp, *, item: Item):
            resp.media = item.model_dump()

        # Valid request
        r = api.requests.post("/items", json={"name": "thing", "price": 9.99})
        assert r.status_code == 200
        assert r.json() == {"name": "thing", "price": 9.99}

        # Missing required field
        r = api.requests.post("/items", json={"name": "thing"})
        assert r.status_code == 422
        assert "errors" in r.json()

The ``request_model=`` decorator argument is the older, equivalent style. It
stores the validated model on ``req.state.validated`` instead of injecting
it, and still returns ``422`` on bad input::

    def test_request_model(api):
        @api.route("/items", methods=["POST"], request_model=Item)
        async def create(req, resp):
            resp.media = req.state.validated.model_dump()

        r = api.requests.post("/items", json={"name": "thing"})
        assert r.status_code == 422

Query strings, headers, and cookies validate the same way, through the typed
parameter markers ``Query``, ``Header``, ``Cookie``, and ``Path`` (see the
:doc:`tour <tour>`). A missing required value or a failed coercion is a
``422`` too::

    from responder import Query

    def test_query_validation(api):
        @api.route("/search")
        def search(req, resp, *, q: str = Query(...), limit: int = Query(10)):
            resp.media = {"q": q, "limit": limit}

        # limit is coerced from the query string to an int
        r = api.requests.get("/search?q=hi&limit=5")
        assert r.json() == {"q": "hi", "limit": 5}

        # the required q is missing -> 422
        r = api.requests.get("/search")
        assert r.status_code == 422
        assert "errors" in r.json()


Testing File Uploads
--------------------

File uploads use the ``files`` parameter, just like the ``requests``
library. Each file is a tuple of ``(filename, content, content_type)``::

    def test_upload(api):
        @api.route("/upload")
        async def upload(req, resp):
            files = await req.media("files")
            resp.media = {"received": list(files.keys())}

        files = {"doc": ("report.pdf", b"content", "application/pdf")}
        r = api.requests.post(api.url_for(upload), files=files)
        assert r.json() == {"received": ["doc"]}


Testing Headers and Cookies
---------------------------

Check response headers and cookies just like you would with any HTTP
client::

    def test_headers(api):
        @api.route("/")
        def view(req, resp):
            resp.headers["X-Custom"] = "hello"
            resp.cookies["session"] = "abc123"

        r = api.requests.get("/")
        assert r.headers["X-Custom"] == "hello"
        assert "session" in r.cookies


Testing Sessions
----------------

Sessions need a signing key, and in v5 that key is mandatory: the old public
default now raises, and an instance built without one mints a random
per-process key (fine for a quick script, useless across a restart). So pass
a real ``secret_key``. Because the test client speaks ``http://``, also pass
``session_https_only=False`` — otherwise the (Secure by default) session
cookie won't round-trip::

    def test_session():
        api = responder.API(
            secret_key="test-secret-key-1234",
            session_https_only=False,
        )

        @api.route("/login", methods=["POST"])
        def login(req, resp):
            resp.session["user"] = "kenneth"

        @api.route("/me")
        def me(req, resp):
            resp.media = {"user": req.session.get("user")}

        # The test client persists cookies, so the session carries over.
        api.requests.post("/login")
        r = api.requests.get("/me")
        assert r.json() == {"user": "kenneth"}

``resp.session`` is a write-through view of ``req.session``, so assigning a
whole dict (``resp.session = {"user": "kenneth"}``) persists as well. See the
:doc:`configuration guide <guide-config>` for how the signing key is resolved
in production.


Testing Dependencies
--------------------

Dependencies resolve per request, so a route that uses one needs no special
wiring — register the provider, call the route. And because registering a
name again replaces the previous provider, you can swap in a fake (a stub
database, a fixed clock) right inside the test::

    def test_dependency(api):
        @api.dependency()
        def db():
            return {"alice": {"name": "Alice"}}

        @api.route("/users/{user_id}")
        def get_user(req, resp, *, user_id, db):
            resp.media = db.get(user_id, {})

        r = api.requests.get("/users/alice")
        assert r.json() == {"name": "Alice"}

Generator providers run their teardown after the response is sent, so any
assertion about cleanup belongs *after* the request returns. App-scoped
providers (``scope="app"``) only tear down on lifespan shutdown — enter the
client as a context manager (``with api.requests as session:``) so the
shutdown actually fires. A Pydantic body parameter always wins over a
same-named dependency, so an injected body is never shadowed by a provider.


Testing WebSockets
------------------

WebSocket tests use Starlette's ``TestClient`` directly, since WebSocket
connections require a different protocol. The ``websocket_connect`` context
manager gives you a connection you can send and receive on::

    from starlette.testclient import TestClient

    def test_websocket(api):
        @api.route("/ws", websocket=True)
        async def ws(ws):
            await ws.accept()
            name = await ws.receive_text()
            await ws.send_text(f"hello, {name}!")
            await ws.close()

        client = TestClient(api)
        with client.websocket_connect("/ws") as ws:
            ws.send_text("world")
            assert ws.receive_text() == "hello, world!"


Testing Error Handling
----------------------

By default, the test client raises exceptions from your route handlers,
which is usually what you want — it makes bugs obvious. But when you're
testing error handling specifically, you want to see the error response
instead. Disable exception propagation with ``raise_server_exceptions``::

    from starlette.testclient import TestClient

    def test_500(api):
        @api.route("/fail")
        def fail(req, resp):
            raise ValueError("something broke")

        client = TestClient(api, raise_server_exceptions=False)
        r = client.get(api.url_for(fail))
        assert r.status_code == 500

If you've registered a custom exception handler, you can test that too::

    def test_custom_error(api):
        @api.exception_handler(ValueError)
        async def handle(req, resp, exc):
            resp.status_code = 400
            resp.media = {"error": str(exc)}

        @api.route("/fail")
        def fail(req, resp):
            raise ValueError("bad input")

        client = TestClient(api, raise_server_exceptions=False)
        r = client.get(api.url_for(fail))
        assert r.status_code == 400
        assert r.json() == {"error": "bad input"}

Two v5 conveniences make error tests shorter. ``responder.abort()`` raises a
rendered HTTP error from anywhere in a handler — for a JSON client its body is
``{"error": <detail>}``, and because it's a regular ``HTTPException`` the test
client returns it as a response (no ``raise_server_exceptions=False`` needed)::

    from responder import abort

    def test_abort(api):
        @api.route("/admin")
        def admin(req, resp):
            abort(403, detail="Forbidden")

        r = api.requests.get("/admin", headers={"Accept": "application/json"})
        assert r.status_code == 403
        assert r.json() == {"error": "Forbidden"}

And ``api.add_exception_handler(exc_or_status, handler)`` is the imperative
twin of the ``@api.exception_handler`` decorator — handy for wiring handlers
inside a fixture. It accepts an exception class or an integer status code.


Testing Lifespan Events
-----------------------

If your app uses startup and shutdown events (for database connections,
caches, etc.), you need the test client to trigger them. Wrap the client
in a ``with`` block — startup runs on enter, shutdown runs on exit::

    def test_with_lifespan(api):
        started = {"value": False}

        @api.on_event("startup")
        async def on_startup():
            started["value"] = True

        @api.route("/")
        def check(req, resp):
            resp.media = {"started": started["value"]}

        with api.requests as session:
            r = session.get("http://localhost/")
            assert r.json() == {"started": True}

Without the ``with`` block, lifespan events won't fire, which can lead to
confusing test failures if your routes depend on startup initialization.


Testing Before and After Hooks
------------------------------

Before-request and after-request hooks run automatically during tests,
just like in production. You can verify their effects on the response::

    def test_hooks(api):
        @api.before_request
        def add_version(req, resp):
            resp.headers["X-Version"] = "5.0"

        @api.after_request
        def add_timing(req, resp):
            resp.headers["X-Served-By"] = "responder"

        @api.route("/")
        def view(req, resp):
            resp.text = "ok"

        r = api.requests.get("/")
        assert r.headers["X-Version"] == "5.0"
        assert r.headers["X-Served-By"] == "responder"


Testing Rate Limiting
---------------------

Rate limiters are just hooks — they run automatically during tests.
Verify the headers and the 429 response::

    from responder.ext.ratelimit import RateLimiter

    def test_rate_limiting():
        api = responder.API(allowed_hosts=["localhost"], sessions=False)
        limiter = RateLimiter(requests=2, period=60)
        limiter.install(api)

        @api.route("/")
        def view(req, resp):
            resp.text = "ok"

        # First two requests succeed
        for _ in range(2):
            r = api.requests.get("http://localhost/")
            assert r.status_code == 200
            assert "X-RateLimit-Remaining" in r.headers

        # Third request is rate limited
        r = api.requests.get("http://localhost/")
        assert r.status_code == 429


Testing Mounted Apps
--------------------

When testing WSGI apps mounted at a subroute, use ``localhost`` as the
host to avoid Werkzeug's trusted host validation::

    from flask import Flask

    def test_flask_mount():
        api = responder.API(allowed_hosts=["localhost"], sessions=False)

        flask_app = Flask(__name__)
        @flask_app.route("/")
        def hello():
            return "Hello from Flask!"

        api.mount("/flask", flask_app)

        r = api.requests.get("http://localhost/flask")
        assert r.status_code == 200
        assert "Hello from Flask" in r.text


Tips
----

- **Keep tests fast.** The in-process test client is already fast — no
  network overhead. Avoid ``time.sleep()`` in tests.

- **One API per test** when testing configuration. If you need a specific
  ``API()`` configuration (like ``cors=True``), create a new instance
  in the test rather than sharing the fixture.

- Use ``api.url_for()`` instead of hard-coded paths. It's a small
  thing, but it makes refactoring painless.

- **Test the contract, not the implementation.** Assert on status codes,
  response bodies, and headers — not on internal state.

- **Use ``localhost`` for mounted WSGI apps.** Werkzeug 3.1.7+ validates
  the ``Host`` header, so avoid synthetic hosts like ``;`` in tests.

- **Quiet the session-key warning.** Any ``API()`` built without a
  ``secret_key`` mints a random per-process key and logs a warning. In tests
  that don't touch sessions, pass ``sessions=False``; in tests that do, pass a
  real ``secret_key=...`` (plus ``session_https_only=False`` so the cookie
  round-trips over ``http``).

- **``req.method`` is uppercase.** It returns ``"GET"`` / ``"POST"``. The
  legacy lowercase comparison (``req.method == "get"``) still works but emits
  a ``DeprecationWarning``, so a suite run with ``pytest -W error`` will fail
  on it — compare against the uppercase form (``== "GET"``).
