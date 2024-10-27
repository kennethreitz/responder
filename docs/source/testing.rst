Building and Testing with Responder
===================================

Responder comes with a first-class, well supported test client for your ASGI web services: **Requests**.

Here, we'll go over the basics of setting up a proper Python package and adding testing to it.

The Basics
----------

Your repository should look like this::

    Pipfile   Pipfile.lock   api.py  test_api.py

``$ cat api.py``::

    import responder

    api = responder.API()

    @api.route("/")
    def hello_world(req, resp):
        resp.text = "hello, world!"

    if __name__ == "__main__":
        api.run()


``$ cat Pipfile``::

    [[source]]
    url = "https://pypi.org/simple"
    verify_ssl = true
    name = "pypi"

    [packages]
    responder = "*"

    [dev-packages]
    pytest = "*"

    [requires]
    python_version = "3.7"

    [pipenv]
    allow_prereleases = true

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


Proper Python Package
---------------------

Optionally, you can not rely on relative imports, and instead install your api as a proper package. This requires:

1. A `proper setup.py <https://github.com/kennethreitz/setup.py>`_ file.
2. ``$ pipenv install -e . --dev``

This will allow you to only specify your dependencies once: in ``setup.py``. ``$ pipenv lock`` will automatically lock your transitive dependencies (e.g. Responder), even if it's not specified in the ``Pipfile``.

This will ensure that your application gets installed in every developer's environment, using Pipenv.
