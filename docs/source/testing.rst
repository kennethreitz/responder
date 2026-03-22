Testing
=======

Responder includes a built-in test client powered by Starlette's TestClient.

Basic Test
----------

``api.py``::

    import responder

    api = responder.API()

    @api.route("/")
    def hello(req, resp):
        resp.text = "hello, world!"

    if __name__ == "__main__":
        api.run()

``test_api.py``::

    import api as service

    def test_hello():
        r = service.api.requests.get("/")
        assert r.text == "hello, world!"

Run with pytest::

    $ pytest


Using Fixtures
--------------

::

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


Testing WebSockets
------------------

::

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


Testing File Uploads
--------------------

::

    def test_upload(api):
        @api.route("/upload")
        async def upload(req, resp):
            files = await req.media("files")
            resp.media = {"name": list(files.keys())[0]}

        files = {"doc": ("test.txt", b"content", "text/plain")}
        r = api.requests.post(api.url_for(upload), files=files)
        assert r.json() == {"name": "doc"}
