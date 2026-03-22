Testing
=======

Responder includes a built-in test client powered by Starlette's
``TestClient``. You don't need to start a server — tests run in-process,
making them fast and reliable.


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

You can test it with pytest::

    # test_api.py
    import api as service

    def test_hello():
        r = service.api.requests.get("/")
        assert r.text == "hello, world!"

Run your tests::

    $ pytest


Using Fixtures
--------------

For larger test suites, use pytest fixtures to share the API instance
across tests::

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
so you don't have to hard-code paths in your tests.


Testing JSON APIs
-----------------

Send JSON data and check the response::

    def test_create_item(api):
        @api.route("/items")
        async def create(req, resp):
            data = await req.media()
            resp.media = {"created": data}
            resp.status_code = 201

        r = api.requests.post(api.url_for(create), json={"name": "widget"})
        assert r.status_code == 201
        assert r.json() == {"created": {"name": "widget"}}


Testing File Uploads
--------------------

Send files using the ``files`` parameter::

    def test_upload(api):
        @api.route("/upload")
        async def upload(req, resp):
            files = await req.media("files")
            resp.media = {"received": list(files.keys())}

        files = {"doc": ("report.pdf", b"content", "application/pdf")}
        r = api.requests.post(api.url_for(upload), files=files)
        assert r.json() == {"received": ["doc"]}


Testing WebSockets
------------------

Use Starlette's ``TestClient`` directly for WebSocket connections::

    from starlette.testclient import TestClient

    def test_websocket(api):
        @api.route("/ws", websocket=True)
        async def ws(ws):
            await ws.accept()
            await ws.send_text("hello")
            await ws.close()

        client = TestClient(api)
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_text() == "hello"


Testing Error Handling
----------------------

To test error responses without pytest raising the exception, disable
server exception propagation::

    from starlette.testclient import TestClient

    def test_500(api):
        @api.route("/fail")
        def fail(req, resp):
            raise ValueError("something broke")

        client = TestClient(api, raise_server_exceptions=False)
        r = client.get(api.url_for(fail))
        assert r.status_code == 500


Testing Lifespan Events
-----------------------

The test client supports lifespan events. Use ``with`` to ensure startup
and shutdown hooks run::

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
