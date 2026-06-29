"""v5.4: api.dependency_overrides() test seam."""

from starlette.testclient import TestClient

import responder


def _app():
    api = responder.API(allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False)

    @api.dependency()
    def db():
        return "real-db"

    @api.dependency(scope="app")
    def config():
        return {"env": "prod"}

    @api.route("/db")
    def db_view(req, resp, *, db):
        resp.media = {"db": db}

    @api.route("/config")
    def cfg_view(req, resp, *, config):
        resp.media = config

    return api, TestClient(api, base_url="http://;")


def test_bare_value_override_and_restore():
    api, client = _app()
    assert client.get("/db").json() == {"db": "real-db"}
    with api.dependency_overrides(db="fake-db"):
        assert client.get("/db").json() == {"db": "fake-db"}
    assert client.get("/db").json() == {"db": "real-db"}


def test_override_bypasses_cached_app_scope():
    api, client = _app()
    assert client.get("/config").json() == {"env": "prod"}  # resolve + cache
    with api.dependency_overrides(config={"env": "test"}):
        assert client.get("/config").json() == {"env": "test"}
    assert client.get("/config").json() == {"env": "prod"}


def test_provider_callable_override_gets_request():
    api, client = _app()

    def fake(req):
        return f"fake:{req.url.path}"

    with api.dependency_overrides(db=fake):
        assert client.get("/db").json() == {"db": "fake:/db"}


def test_override_restored_even_on_exception():
    api, client = _app()
    try:
        with api.dependency_overrides(db="x"):
            raise ValueError("boom")
    except ValueError:
        pass
    assert client.get("/db").json() == {"db": "real-db"}
