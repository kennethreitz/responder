Building and Testing with Responder
===================================

Responder comes with a first-class, well supported test client for your ASGI web services (powered by Starlette's TestClient).

Here, we'll go over the basics of setting up and testing a Responder application.

The Basics
----------

Your project should look like this::

    api.py  test_api.py

``$ cat api.py``::

    import responder

    api = responder.API()

    @api.route("/")
    def hello_world(req, resp):
        resp.text = "hello, world!"

    if __name__ == "__main__":
        api.run()

Writing Tests
-------------

``$ cat test_api.py``::

    import pytest
    import api as service

    @pytest.fixture
    def api():
        return service.api


    def test_hello_world(api):
        r = api.requests.get("/")
        assert r.text == "hello, world!"

``$ pytest``::

    ...
    ========================== 1 passed in 0.10 seconds ==========================
