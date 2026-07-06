"""v9.0: generated OpenAPI documents framework operational responses."""

import yaml
from openapi_spec_validator import validate
from starlette.testclient import TestClient

import responder
from responder.ext.ratelimit import RateLimiter, RedisBackend


def _api(**kwargs):
    kwargs.setdefault("openapi", "3.1.0")
    kwargs.setdefault("secret_key", "x" * 32)
    kwargs.setdefault("allowed_hosts", [";"])
    kwargs.setdefault("session_https_only", False)
    return responder.API(**kwargs)


def _schema(api):
    response = TestClient(api, base_url="http://;").get("/schema.yml")
    assert response.status_code == 200
    return yaml.safe_load(response.content)


def test_openapi_documents_csrf_only_on_protected_unsafe_routes():
    api = _api(csrf=True)

    @api.get("/form")
    def form(req, resp):
        resp.text = str(req.csrf_input)

    @api.post("/submit")
    def submit(req, resp):
        resp.media = {"ok": True}

    @api.post("/webhook", csrf=False)
    def webhook(req, resp):
        resp.media = {"ok": True}

    spec = _schema(api)

    form_responses = spec["paths"]["/form"]["get"]["responses"]
    submit_responses = spec["paths"]["/submit"]["post"]["responses"]
    webhook_responses = spec["paths"]["/webhook"]["post"]["responses"]

    assert "403" not in form_responses
    assert "403" in submit_responses
    assert "401" not in submit_responses
    assert "403" not in webhook_responses
    assert "application/problem+json" in submit_responses["403"]["content"]
    validate(spec)


def test_openapi_documents_route_level_rate_limits():
    api = _api()
    limiter = RateLimiter(requests=1, period=60)

    @api.get("/limited")
    @limiter.limit
    def limited(req, resp):
        resp.media = {"ok": True}

    @api.get("/open")
    def open_route(req, resp):
        resp.media = {"ok": True}

    spec = _schema(api)
    limited_responses = spec["paths"]["/limited"]["get"]["responses"]
    open_responses = spec["paths"]["/open"]["get"]["responses"]

    assert "429" in limited_responses
    assert "503" in limited_responses
    assert "application/problem+json" in limited_responses["429"]["content"]
    assert "429" not in open_responses
    assert "503" not in open_responses
    validate(spec)


def test_openapi_documents_installed_rate_limiter_on_all_routes():
    api = _api()
    RateLimiter(requests=1, period=60).install(api)

    @api.get("/one")
    def one(req, resp):
        resp.media = {"ok": True}

    @api.post("/two")
    def two(req, resp):
        resp.media = {"ok": True}

    spec = _schema(api)

    assert "429" in spec["paths"]["/one"]["get"]["responses"]
    assert "429" in spec["paths"]["/two"]["post"]["responses"]
    validate(spec)


def test_openapi_omits_413_when_request_body_limit_disabled():
    api = _api(max_request_size=None)

    @api.post("/items")
    def items(req, resp):
        resp.media = {"ok": True}

    spec = _schema(api)

    assert "413" not in spec["paths"]["/items"]["post"]["responses"]
    validate(spec)


def test_rate_limiter_uses_problem_details_by_default():
    api = _api()
    RateLimiter(requests=1, period=60).install(api)

    @api.get("/")
    def index(req, resp):
        resp.text = "ok"

    client = TestClient(api, base_url="http://;")
    assert client.get("/").status_code == 200

    response = client.get("/")

    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["Retry-After"] == "60"
    assert response.json()["title"] == "Too Many Requests"


def test_rate_limiter_keeps_legacy_error_shape_when_problem_details_disabled():
    api = _api(problem_details=False)
    RateLimiter(requests=1, period=60).install(api)

    @api.get("/")
    def index(req, resp):
        resp.text = "ok"

    client = TestClient(api, base_url="http://;")
    assert client.get("/").status_code == 200

    response = client.get("/")

    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"error": "rate limit exceeded"}


def test_rate_limiter_backend_failure_uses_problem_details():
    class DownRedis:
        def eval(self, *args, **kwargs):
            raise RuntimeError("down")

    api = _api()
    RateLimiter(
        requests=1, period=60, backend=RedisBackend(client=DownRedis())
    ).install(api)

    @api.get("/")
    def index(req, resp):
        resp.text = "never"

    response = TestClient(api, base_url="http://;").get("/")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["Retry-After"] == "60"
    assert response.json()["title"] == "Service Unavailable"
