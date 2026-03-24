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

If you're using Pydantic models for request validation, you can test
that invalid inputs are properly rejected::

    from pydantic import BaseModel

    class Item(BaseModel):
        name: str
        price: float

    def test_validation(api):
        @api.route("/items", methods=["POST"], request_model=Item)
        async def create(req, resp):
            data = await req.media()
            resp.media = data

        # Valid request
        r = api.requests.post("/items", json={"name": "thing", "price": 9.99})
        assert r.status_code == 200

        # Missing required field
        r = api.requests.post("/items", json={"name": "thing"})
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
----------------------------

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
            r = session.get("http://;/")
            assert r.json() == {"started": True}

Without the ``with`` block, lifespan events won't fire, which can lead to
confusing test failures if your routes depend on startup initialization.


Testing Before and After Hooks
------------------------------

Before-request and after-request hooks run automatically during tests,
just like in production. You can verify their effects on the response::

    def test_hooks(api):
        @api.route(before_request=True)
        def add_version(req, resp):
            resp.headers["X-Version"] = "3.2"

        @api.after_request()
        def add_timing(req, resp):
            resp.headers["X-Served-By"] = "responder"

        @api.route("/")
        def view(req, resp):
            resp.text = "ok"

        r = api.requests.get("/")
        assert r.headers["X-Version"] == "3.2"
        assert r.headers["X-Served-By"] == "responder"


Testing Rate Limiting
---------------------

Rate limiters are just hooks — they run automatically during tests.
Verify the headers and the 429 response::

    from responder.ext.ratelimit import RateLimiter

    def test_rate_limiting():
        api = responder.API(allowed_hosts=["localhost"])
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
        api = responder.API(allowed_hosts=["localhost"])

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
