"""v5.4: add_health_check + readiness endpoint."""

import yaml
from starlette.testclient import TestClient

import responder


def _client(api):
    return TestClient(api, base_url="http://;")


def test_all_checks_pass():
    api = responder.API(allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False)
    api.add_health_check("db", lambda: True)
    api.add_health_check("cache", lambda: True)
    r = _client(api).get("/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "checks": {"db": {"status": "ok"}, "cache": {"status": "ok"}},
    }


def test_failing_check_returns_503():
    api = responder.API(allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False)
    api.add_health_check("db", lambda: False)
    r = _client(api).get("/health")
    assert r.status_code == 503
    assert r.json()["checks"]["db"]["status"] == "error"


def test_raising_check_reports_detail():
    api = responder.API(allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False)

    def boom():
        raise RuntimeError("connection refused")

    api.add_health_check("db", boom)
    r = _client(api).get("/health")
    assert r.status_code == 503
    assert r.json()["checks"]["db"]["detail"] == "connection refused"


def test_async_check():
    api = responder.API(allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False)

    async def check():
        return True

    api.add_health_check("svc", check)
    assert _client(api).get("/health").status_code == 200


def test_configurable_route_and_schema_exclusion():
    api = responder.API(
        title="T", version="1", openapi="3.0.2",
        allowed_hosts=[";"], session_https_only=False, health_route="/healthz",
    )
    api.add_health_check("ok", lambda: True)
    client = _client(api)
    assert client.get("/healthz").status_code == 200
    spec = yaml.safe_load(client.get("/schema.yml").content)
    assert "/healthz" not in spec.get("paths", {})
