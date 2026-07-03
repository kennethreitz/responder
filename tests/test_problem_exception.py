"""Tests for the raisable RFC 9457 ``responder.Problem`` exception."""

import datetime as dt

import pytest
from starlette.exceptions import HTTPException
from starlette.testclient import TestClient

import responder
from responder import Problem, abort


def _api(**kwargs):
    return responder.API(
        title="T",
        version="1",
        secret_key="x" * 32,
        allowed_hosts=[";"],
        session_https_only=False,
        **kwargs,
    )


def _client(api):
    return TestClient(api, base_url="http://;", raise_server_exceptions=False)


def test_problem_is_an_http_exception():
    exc = Problem(409)
    assert isinstance(exc, HTTPException)
    assert exc.status_code == 409
    # Starlette fills detail with the status phrase.
    assert exc.detail == "Conflict"


def test_problem_renders_full_rfc9457_payload():
    api = _api()

    @api.route("/quota")
    def quota(req, resp):
        raise Problem(
            409,
            "You have used all 100 requests for today.",
            title="Quota Exceeded",
            type="https://api.example.com/errors/quota-exceeded",
            instance="/account/12345/quota",
            balance=0,
        )

    r = _client(api).get("/quota")
    assert r.status_code == 409
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"] == "https://api.example.com/errors/quota-exceeded"
    assert body["title"] == "Quota Exceeded"
    assert body["status"] == 409
    assert body["detail"] == "You have used all 100 requests for today."
    assert body["instance"] == "/account/12345/quota"
    assert body["balance"] == 0


def test_problem_defaults_title_and_instance():
    api = _api()

    @api.route("/things/{id}")
    def thing(req, resp, *, id):
        raise Problem(404, "No such thing.")

    body = _client(api).get("/things/42").json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Not Found"
    assert body["detail"] == "No such thing."
    # instance defaults to the request path per the RFC 9457 recommendation.
    assert body["instance"] == "/things/42"


def test_plain_http_exception_payload_unchanged():
    # No instance member sneaks into non-Problem error payloads.
    api = _api()

    @api.route("/boom")
    def boom(req, resp):
        abort(404, detail="gone")

    body = _client(api).get("/boom").json()
    assert body["title"] == "Not Found"
    assert "instance" not in body
    assert body["type"] == "about:blank"


def test_problem_headers_are_sent():
    api = _api()

    @api.route("/limited")
    def limited(req, resp):
        raise Problem(429, "Slow down.", headers={"Retry-After": "30"})

    r = _client(api).get("/limited")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "30"


def test_problem_errors_list():
    api = _api()

    @api.route("/validate")
    def validate(req, resp):
        raise Problem(
            422,
            "Validation failed.",
            errors=[{"loc": ["body", "name"], "msg": "field required"}],
        )

    body = _client(api).get("/validate").json()
    assert body["errors"] == [{"loc": ["body", "name"], "msg": "field required"}]


def test_problem_handler_enrichment_applies_and_explicit_fields_win():
    def enrich(payload):
        payload["support"] = "help@example.com"
        payload["type"] = "https://example.com/handler-type"
        return payload

    api = _api(problem_handler=enrich)

    @api.route("/conflict")
    def conflict(req, resp):
        raise Problem(409, type="https://example.com/explicit-type")

    body = _client(api).get("/conflict").json()
    # Enrichment ran...
    assert body["support"] == "help@example.com"
    # ...but the Problem's explicit members layer on top.
    assert body["type"] == "https://example.com/explicit-type"


def test_problem_handler_can_set_instance_when_unset():
    def enrich(payload):
        payload["instance"] = "/from-handler"
        return payload

    api = _api(problem_handler=enrich)

    @api.route("/thing")
    def thing(req, resp):
        raise Problem(410)

    body = _client(api).get("/thing").json()
    # A handler-provided instance beats the request-path default.
    assert body["instance"] == "/from-handler"


def test_problem_extensions_use_json_encoder():
    api = _api()

    @api.route("/dated")
    def dated(req, resp):
        raise Problem(410, "Expired.", expired_at=dt.datetime(2026, 1, 1, 12, 0))

    body = _client(api).get("/dated").json()
    assert body["expired_at"] == "2026-01-01T12:00:00"


def test_problem_extensions_use_custom_encoder():
    class Money:
        def __init__(self, amount):
            self.amount = amount

    def encode(obj):
        if isinstance(obj, Money):
            return f"${obj.amount}"
        raise TypeError(f"cannot encode {type(obj).__name__}")

    api = _api(encoder=encode)

    @api.route("/priced")
    def priced(req, resp):
        raise Problem(402, "Payment required.", price=Money(5))

    body = _client(api).get("/priced").json()
    assert body["price"] == "$5"


def test_problem_from_dependency():
    api = _api()

    def guard():
        raise Problem(403, "Nope.", type="https://example.com/forbidden")

    @api.route("/guarded", dependencies=[responder.Depends(guard)])
    def guarded(req, resp):
        resp.media = {"ok": True}

    r = _client(api).get("/guarded")
    assert r.status_code == 403
    assert r.json()["type"] == "https://example.com/forbidden"


def test_problem_from_before_request_hook():
    api = _api()

    @api.route(before_request=True)
    def hook(req, resp):
        if req.url.path == "/blocked":
            raise Problem(451, "Unavailable for legal reasons.")

    @api.route("/blocked")
    def blocked(req, resp):
        resp.media = {"ok": True}

    r = _client(api).get("/blocked")
    assert r.status_code == 451
    assert r.json()["status"] == 451


def test_problem_from_async_view():
    api = _api()

    @api.route("/async")
    async def view(req, resp):
        raise Problem(409, "Async conflict.", attempt=3)

    body = _client(api).get("/async").json()
    assert body["detail"] == "Async conflict."
    assert body["attempt"] == 3


def test_problem_legacy_mode_uses_legacy_contract():
    api = _api(problem_details=False)

    @api.route("/old")
    def old(req, resp):
        raise Problem(409, "Old-style conflict.", balance=0)

    client = _client(api)
    r = client.get("/old", headers={"Accept": "application/json"})
    assert r.status_code == 409
    assert r.json() == {"error": "Old-style conflict."}
    r = client.get("/old", headers={"Accept": "text/plain"})
    assert r.status_code == 409
    assert r.text == "Old-style conflict."


def test_abort_plain_still_raises_http_exception():
    with pytest.raises(HTTPException) as excinfo:
        abort(403, detail="Forbidden")
    assert not isinstance(excinfo.value, Problem)
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "Forbidden"


def test_abort_with_rich_members_raises_problem():
    with pytest.raises(Problem) as excinfo:
        abort(
            409,
            detail="Quota exhausted",
            type="https://api.example.com/errors/quota-exceeded",
            balance=0,
        )
    exc = excinfo.value
    assert exc.type == "https://api.example.com/errors/quota-exceeded"
    assert exc.extensions == {"balance": 0}


def test_abort_rich_members_render_end_to_end():
    api = _api()

    @api.route("/quota")
    def quota(req, resp):
        abort(
            409,
            detail="Quota exhausted",
            title="Quota Exceeded",
            type="https://api.example.com/errors/quota-exceeded",
            balance=0,
        )

    r = _client(api).get("/quota")
    assert r.status_code == 409
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"] == "https://api.example.com/errors/quota-exceeded"
    assert body["title"] == "Quota Exceeded"
    assert body["detail"] == "Quota exhausted"
    assert body["balance"] == 0
    assert body["instance"] == "/quota"


def test_problem_exported_from_package_root():
    assert responder.Problem is Problem
    assert "Problem" in responder.__all__
