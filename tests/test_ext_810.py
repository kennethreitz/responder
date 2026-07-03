"""v8.1: extension improvements.

- ratelimit: ``key=`` custom key function, ``X-RateLimit-Reset``, ``fail_open=``
- metrics: configurable buckets, label escaping, in-flight gauge
- pagination: RFC 8288 ``Link`` + ``X-Total-Count`` headers
- sessions: pluggable Redis serializer that round-trips common types
- logging: unified, validated request-ID handling
"""

import asyncio
import datetime
import decimal
import logging
import re
import uuid
from types import SimpleNamespace

import pytest

import responder
from responder.ext.logging import _resolve_request_id
from responder.ext.metrics import MetricsCollector
from responder.ext.pagination import Page, paginate, set_pagination_headers
from responder.ext.ratelimit import (
    AsyncRedisBackend,
    MemoryBackend,
    RateLimiter,
    RedisBackend,
)
from responder.ext.sessions import (
    _TYPE_TAG,
    AsyncRedisSessionBackend,
    JSONSessionSerializer,
    RedisSessionBackend,
)

UUID4_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)


def _api(**kwargs):
    kwargs.setdefault("allowed_hosts", [";"])
    kwargs.setdefault("session_https_only", False)
    return responder.API(**kwargs)


def _stub_req(headers=None, client=("1.2.3.4", 5678)):
    return SimpleNamespace(client=client, headers=headers or {})


def _stub_resp():
    return SimpleNamespace(headers={}, status_code=200, media=None)


# --------------------------------------------------------------------------
# Rate limiting: X-RateLimit-Reset
# --------------------------------------------------------------------------


def test_memory_backend_hit_returns_reset_after():
    backend = MemoryBackend()
    allowed, remaining, reset_after = backend.hit("k", max_requests=2, period=60)
    assert (allowed, remaining) == (True, 1)
    assert 0 < reset_after <= 60

    backend.hit("k", max_requests=2, period=60)
    allowed, remaining, reset_after = backend.hit("k", max_requests=2, period=60)
    assert (allowed, remaining) == (False, 0)
    assert 0 < reset_after <= 60


def test_reset_header_on_allowed_and_denied_responses():
    api = _api()
    limiter = RateLimiter(requests=1, period=60)
    limiter.install(api)

    @api.route("/")
    def view(req, resp):
        resp.text = "ok"

    r = api.requests.get("/")
    assert r.status_code == 200
    assert 0 < int(r.headers["X-RateLimit-Reset"]) <= 60

    r = api.requests.get("/")
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "60"
    assert 0 < int(r.headers["X-RateLimit-Reset"]) <= 60


def test_two_tuple_backend_still_works_without_reset_header():
    class LegacyBackend:
        def __init__(self):
            self.allowed = True

        def hit(self, key, max_requests, period):
            return (self.allowed, 5 if self.allowed else 0)

    backend = LegacyBackend()
    limiter = RateLimiter(requests=10, period=60, backend=backend)

    req, resp = _stub_req(), _stub_resp()
    assert limiter.check(req, resp) is True
    assert resp.headers["X-RateLimit-Remaining"] == "5"
    assert "X-RateLimit-Reset" not in resp.headers

    backend.allowed = False
    resp = _stub_resp()
    assert limiter.check(req, resp) is False
    assert resp.status_code == 429


def test_redis_backend_surfaces_window_ttl():
    class FakeRedis:
        def eval(self, script, numkeys, key, period):
            return [1, 42]

    backend = RedisBackend(client=FakeRedis())
    assert backend.hit("k", max_requests=5, period=60) == (True, 4, 42)


def test_redis_backend_tolerates_legacy_bare_count_eval():
    class LegacyFakeRedis:
        def eval(self, script, numkeys, key, period):
            return 6  # pre-8.1 fakes return just the count

    backend = RedisBackend(client=LegacyFakeRedis())
    assert backend.hit("k", max_requests=5, period=60) == (False, 0, 60)


def test_async_redis_backend_surfaces_window_ttl():
    class FakeAsyncRedis:
        async def eval(self, script, numkeys, key, period):
            return [2, 30]

    backend = AsyncRedisBackend(client=FakeAsyncRedis())
    result = asyncio.run(backend.ahit("k", max_requests=5, period=60))
    assert result == (True, 3, 30)


# --------------------------------------------------------------------------
# Rate limiting: custom key function
# --------------------------------------------------------------------------


def test_custom_key_function_gives_independent_budgets():
    api = _api()
    limiter = RateLimiter(
        requests=1,
        period=60,
        key=lambda req: req.headers.get("x-api-key", "anon"),
    )
    limiter.install(api)

    @api.route("/")
    def view(req, resp):
        resp.text = "ok"

    assert api.requests.get("/", headers={"x-api-key": "alice"}).status_code == 200
    assert api.requests.get("/", headers={"x-api-key": "alice"}).status_code == 429
    # A different key has its own budget.
    assert api.requests.get("/", headers={"x-api-key": "bob"}).status_code == 200


def test_key_function_buckets_land_in_backend():
    backend = MemoryBackend()
    limiter = RateLimiter(
        requests=5, period=60, backend=backend, key=lambda req: "user:42"
    )
    limiter.check(_stub_req(), _stub_resp())
    assert list(backend._buckets) == ["user:42"]


def test_key_function_falsy_return_falls_back_to_client_ip():
    limiter = RateLimiter(key=lambda req: None)
    assert limiter._client_key(_stub_req()) == "1.2.3.4"
    limiter = RateLimiter(key=lambda req: "")
    assert limiter._client_key(_stub_req()) == "1.2.3.4"


def test_key_function_result_is_stringified():
    limiter = RateLimiter(key=lambda req: 42)
    assert limiter._client_key(_stub_req()) == "42"


# --------------------------------------------------------------------------
# Rate limiting: fail_open
# --------------------------------------------------------------------------


class ExplodingBackend:
    def hit(self, key, max_requests, period):
        raise ConnectionError("redis is down")


class ExplodingAsyncBackend:
    async def ahit(self, key, max_requests, period):
        raise ConnectionError("redis is down")


def test_backend_outage_fails_closed_by_default(caplog):
    limiter = RateLimiter(backend=ExplodingBackend())
    resp = _stub_resp()
    with caplog.at_level(logging.ERROR, logger="responder"):
        assert limiter.check(_stub_req(), resp) is False
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "60"
    assert any("fail_open=False" in r.message for r in caplog.records)


def test_backend_outage_fail_open_allows_with_warning(caplog):
    limiter = RateLimiter(backend=ExplodingBackend(), fail_open=True)
    resp = _stub_resp()
    with caplog.at_level(logging.WARNING, logger="responder"):
        assert limiter.check(_stub_req(), resp) is True
    assert resp.status_code == 200  # untouched
    assert any("fail_open=True" in r.message for r in caplog.records)


def test_backend_outage_fail_open_async(caplog):
    limiter = RateLimiter(backend=ExplodingAsyncBackend(), fail_open=True)
    resp = _stub_resp()
    with caplog.at_level(logging.WARNING, logger="responder"):
        assert asyncio.run(limiter.acheck(_stub_req(), resp)) is True


def test_backend_outage_fail_closed_end_to_end():
    api = _api()
    limiter = RateLimiter(backend=ExplodingBackend())
    limiter.install(api)

    @api.route("/")
    def view(req, resp):
        resp.text = "never"

    r = api.requests.get("/")
    assert r.status_code == 503


def test_backend_outage_fail_open_end_to_end():
    api = _api()
    limiter = RateLimiter(backend=ExplodingBackend(), fail_open=True)
    limiter.install(api)

    @api.route("/")
    def view(req, resp):
        resp.text = "served"

    r = api.requests.get("/")
    assert r.status_code == 200
    assert r.text == "served"


# --------------------------------------------------------------------------
# Metrics: configurable buckets
# --------------------------------------------------------------------------


def test_custom_buckets_render():
    collector = MetricsCollector(buckets=(0.1, 1.0, 30.0))
    collector.record("GET", "/slow", 200, 5.0)
    body = collector.render()
    assert 'le="0.1"} 0' in body
    assert 'le="1.0"} 0' in body
    assert 'le="30.0"} 1' in body
    assert 'le="0.005"' not in body  # default buckets not used
    assert 'le="+Inf"} 1' in body


def test_buckets_must_be_ascending_and_nonempty():
    with pytest.raises(ValueError):
        MetricsCollector(buckets=())
    with pytest.raises(ValueError):
        MetricsCollector(buckets=(1.0, 0.5))
    with pytest.raises(ValueError):
        MetricsCollector(buckets=(1.0, 1.0))


def test_default_buckets_unchanged():
    collector = MetricsCollector()
    collector.record("GET", "/", 200, 0.001)
    assert 'le="0.005"} 1' in collector.render()


# --------------------------------------------------------------------------
# Metrics: label-value escaping
# --------------------------------------------------------------------------


def test_label_values_are_escaped():
    collector = MetricsCollector()
    collector.record("GET", '/pa"th\\with\nnasties', 200, 0.01)
    body = collector.render()
    assert '/pa\\"th\\\\with\\nnasties' in body
    # No raw newline sneaks into the middle of a sample line.
    for line in body.splitlines():
        assert line == "" or line.startswith(("#", "responder_"))


def test_plain_labels_untouched():
    collector = MetricsCollector()
    collector.record("GET", "/users/{id}", 200, 0.01)
    body = collector.render()
    assert (
        'responder_requests_total{method="GET",path="/users/{id}",status="200"} 1'
        in body
    )


# --------------------------------------------------------------------------
# Metrics: in-flight gauge
# --------------------------------------------------------------------------


def test_in_flight_gauge_rendered():
    collector = MetricsCollector()
    body = collector.render()
    assert "# TYPE responder_requests_in_flight gauge" in body
    assert "responder_requests_in_flight 0" in body
    collector.track_in_flight(1)
    assert "responder_requests_in_flight 1" in collector.render()
    collector.track_in_flight(-1)
    assert "responder_requests_in_flight 0" in collector.render()


def test_in_flight_gauge_counts_active_request():
    api = _api(metrics_route="/metrics")
    seen = {}

    @api.route("/observe")
    def observe(req, resp):
        # While this handler runs, its own request is in flight.
        seen["body"] = api.metrics.render()
        resp.text = "ok"

    assert api.requests.get("/observe").status_code == 200
    assert "responder_requests_in_flight 1" in seen["body"]
    # And it decrements once the request completes.
    r = api.requests.get("/metrics")
    assert "responder_requests_in_flight 1\n" in r.text  # the /metrics scrape itself


def test_in_flight_gauge_returns_to_zero():
    api = _api(metrics_route="/metrics")

    @api.route("/")
    def view(req, resp):
        resp.text = "ok"

    api.requests.get("/")
    api.requests.get("/")
    assert api.metrics.in_flight == 0


# --------------------------------------------------------------------------
# Pagination: Link / X-Total-Count headers
# --------------------------------------------------------------------------


def _links(header):
    """Parse a Link header into {rel: url}."""
    out = {}
    for part in header.split(", "):
        url, _, rel = part.partition("; ")
        out[rel.split('"')[1]] = url.strip("<>")
    return out


def test_link_headers_middle_page():
    req = SimpleNamespace(full_url="http://;/items?q=foo&page=3&size=10")
    resp = _stub_resp()
    set_pagination_headers(req, resp, paginate(list(range(55)), page=3, size=10))

    assert resp.headers["X-Total-Count"] == "55"
    links = _links(resp.headers["Link"])
    assert links["first"] == "http://;/items?q=foo&page=1&size=10"
    assert links["prev"] == "http://;/items?q=foo&page=2&size=10"
    assert links["next"] == "http://;/items?q=foo&page=4&size=10"
    assert links["last"] == "http://;/items?q=foo&page=6&size=10"


def test_link_headers_first_page_omits_prev():
    req = SimpleNamespace(full_url="http://;/items?page=1&size=10")
    resp = _stub_resp()
    set_pagination_headers(req, resp, paginate(list(range(25)), page=1, size=10))
    links = _links(resp.headers["Link"])
    assert "prev" not in links
    assert links["next"].endswith("page=2&size=10")
    assert links["last"].endswith("page=3&size=10")


def test_link_headers_last_page_omits_next():
    req = SimpleNamespace(full_url="http://;/items?page=3&size=10")
    resp = _stub_resp()
    set_pagination_headers(req, resp, paginate(list(range(25)), page=3, size=10))
    links = _links(resp.headers["Link"])
    assert "next" not in links
    assert links["prev"].endswith("page=2&size=10")


def test_link_headers_single_and_empty_pages():
    req = SimpleNamespace(full_url="http://;/items")
    resp = _stub_resp()
    set_pagination_headers(req, resp, paginate([1, 2], page=1, size=10))
    links = _links(resp.headers["Link"])
    assert set(links) == {"first", "last"}

    resp = _stub_resp()
    set_pagination_headers(req, resp, paginate([], page=1, size=10))
    links = _links(resp.headers["Link"])
    assert set(links) == {"first", "last"}
    assert links["last"].endswith("page=1&size=10")  # clamped to 1, never 0
    assert resp.headers["X-Total-Count"] == "0"


def test_pagination_headers_end_to_end():
    api = _api()

    @api.route("/items")
    def items(req, resp):
        page = int(req.params.get("page", 1))
        result = paginate(list(range(45)), page=page, size=10)
        set_pagination_headers(req, resp, result)
        resp.media = result

    r = api.requests.get("/items?page=2&size=10&tag=a")
    assert r.status_code == 200
    assert r.headers["X-Total-Count"] == "45"
    links = _links(r.headers["Link"])
    assert set(links) == {"first", "prev", "next", "last"}
    # Foreign query params survive the rewrite.
    assert "tag=a" in links["next"]
    assert "page=3" in links["next"]


def test_set_pagination_headers_exported():
    from responder.ext import pagination

    assert "set_pagination_headers" in pagination.__all__
    assert isinstance(paginate([1], page=1, size=1), Page)


# --------------------------------------------------------------------------
# Sessions: serializer parity between memory and Redis backends
# --------------------------------------------------------------------------

RICH_SESSION = {
    "when": datetime.datetime(2026, 7, 3, 12, 30, 15, 123456),
    "day": datetime.date(2026, 7, 3),
    "at": datetime.time(9, 15, 30),
    "price": decimal.Decimal("19.99"),
    "id": uuid.UUID("12345678-1234-5678-1234-567812345678"),
    "tags": {"a", "b"},
    "frozen": frozenset({1, 2}),
    "blob": b"\x00\x01\xff",
    "nested": {"stamps": [datetime.date(2020, 1, 1)]},
    "plain": {"n": 1, "s": "x", "b": True, "none": None, "list": [1, 2]},
}


def test_json_session_serializer_roundtrips_rich_types():
    codec = JSONSessionSerializer()
    assert codec.loads(codec.dumps(RICH_SESSION)) == RICH_SESSION


def test_json_session_serializer_rejects_unknown_types():
    class Custom:
        pass

    codec = JSONSessionSerializer()
    with pytest.raises(TypeError, match="serializer"):
        codec.dumps({"x": Custom()})


class FakeRedisKV:
    """get/setex/expire/delete stand-in for redis-py (sync)."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value

    def expire(self, key, ttl):
        pass

    def delete(self, key):
        self.store.pop(key, None)


class FakeAsyncRedisKV(FakeRedisKV):
    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value

    async def expire(self, key, ttl):
        pass

    async def delete(self, key):
        self.store.pop(key, None)


def test_redis_session_backend_roundtrips_rich_types():
    backend = RedisSessionBackend(client=FakeRedisKV())
    backend.set("sid", RICH_SESSION, max_age=60)
    assert backend.get("sid") == RICH_SESSION


def test_async_redis_session_backend_roundtrips_rich_types():
    backend = AsyncRedisSessionBackend(client=FakeAsyncRedisKV())

    async def scenario():
        await backend.aset("sid", RICH_SESSION, max_age=60)
        return await backend.aget("sid")

    assert asyncio.run(scenario()) == RICH_SESSION


def test_redis_session_backend_accepts_custom_serializer():
    class UpperCodec:
        def dumps(self, data):
            return repr(data).upper()

        def loads(self, raw):
            return {"raw": raw}

    fake = FakeRedisKV()
    backend = RedisSessionBackend(client=fake, serializer=UpperCodec())
    backend.set("sid", {"k": "v"}, max_age=60)
    (stored,) = fake.store.values()
    assert stored == "{'K': 'V'}"
    assert backend.get("sid") == {"raw": "{'K': 'V'}"}


def test_session_datetime_survives_redis_end_to_end():
    api = _api(session_backend=RedisSessionBackend(client=FakeRedisKV()))
    stamp = datetime.datetime(2026, 7, 3, 8, 0, 0)

    @api.route("/login")
    def login(req, resp):
        req.session["when"] = stamp
        req.session["roles"] = {"admin"}
        resp.text = "ok"

    @api.route("/check")
    def check(req, resp):
        resp.media = {
            "type": type(req.session["when"]).__name__,
            "iso": req.session["when"].isoformat(),
            "roles_type": type(req.session["roles"]).__name__,
        }

    with api.requests as client:
        assert client.get("/login").status_code == 200
        data = client.get("/check").json()
    assert data == {
        "type": "datetime",
        "iso": "2026-07-03T08:00:00",
        "roles_type": "set",
    }


# --------------------------------------------------------------------------
# Logging: unified request-ID handling
# --------------------------------------------------------------------------


def test_resolve_request_id_honors_valid_inbound():
    assert _resolve_request_id({b"x-request-id": b"trace-123.abc_DEF"}) == (
        "trace-123.abc_DEF"
    )


@pytest.mark.parametrize(
    "inbound",
    [
        b"",  # missing / empty
        b"x" * 129,  # too long
        b"has space",
        b"newline\ninjection",
        b"\xff\xfe",  # non-ASCII bytes
        b"tab\there",  # control whitespace
        b"nul\x00byte",
    ],
)
def test_resolve_request_id_mints_uuid_for_invalid_inbound(inbound):
    rid = _resolve_request_id({b"x-request-id": inbound})
    assert UUID4_RE.fullmatch(rid)


def test_resolve_request_id_accepts_128_chars():
    assert _resolve_request_id({b"x-request-id": b"a" * 128}) == "a" * 128


@pytest.mark.parametrize("config", [{"request_id": True}, {"enable_logging": True}])
def test_middlewares_mint_identical_uuid_format(config):
    api = _api(**config)

    @api.route("/")
    def view(req, resp):
        resp.text = "ok"

    r = api.requests.get("/")
    assert UUID4_RE.fullmatch(r.headers["X-Request-ID"])


@pytest.mark.parametrize("config", [{"request_id": True}, {"enable_logging": True}])
def test_middlewares_reject_oversized_inbound_id(config):
    api = _api(**config)

    @api.route("/")
    def view(req, resp):
        resp.text = "ok"

    huge = "A" * 8192
    r = api.requests.get("/", headers={"X-Request-ID": huge})
    assert r.headers["X-Request-ID"] != huge
    assert UUID4_RE.fullmatch(r.headers["X-Request-ID"])


@pytest.mark.parametrize("config", [{"request_id": True}, {"enable_logging": True}])
def test_middlewares_echo_valid_inbound_id(config):
    api = _api(**config)

    @api.route("/")
    def view(req, resp):
        resp.text = "ok"

    r = api.requests.get("/", headers={"X-Request-ID": "svc-a.7f3c"})
    assert r.headers["X-Request-ID"] == "svc-a.7f3c"


def test_setup_logging_is_exported():
    from responder.ext import logging as logging_ext

    assert "setup_logging" in logging_ext.__all__
    assert callable(logging_ext.setup_logging)


# --------------------------------------------------------------------------
# Finding 8: session serializer must not let a reserved tag key poison data
# --------------------------------------------------------------------------


def test_session_serializer_escapes_user_dict_with_reserved_tag():
    """A user dict carrying the reserved tag key round-trips verbatim instead
    of being silently revived into a datetime/etc."""
    codec = JSONSessionSerializer()
    hostile = {
        "user": {_TYPE_TAG: "datetime", "value": "2020-01-01T00:00:00"},
        "bare": {_TYPE_TAG: "foo"},
        "with_extra": {_TYPE_TAG: "uuid", "value": "x", "extra": 1},
        # data that already looks pre-escaped must survive too (double escape).
        "pre": {"__esc__" + _TYPE_TAG: "already"},
    }
    revived = codec.loads(codec.dumps(hostile))
    assert revived == hostile
    # The nested value must stay a plain dict, never a datetime.
    assert isinstance(revived["user"], dict)


def test_session_serializer_survives_malformed_tagged_value():
    """A malformed tagged value must not raise on load (it would 500 every
    request until the session was cleared)."""
    codec = JSONSessionSerializer()
    poison = '{"' + _TYPE_TAG + '": "datetime", "value": "not-a-date"}'
    # No exception, and the raw dict is handed back untouched.
    assert codec.loads(poison) == {_TYPE_TAG: "datetime", "value": "not-a-date"}


def test_session_serializer_reserved_tag_alongside_real_types():
    """Escaping a reserved-tag user dict must not disturb genuine rich types
    stored beside (or nested within) it."""
    codec = JSONSessionSerializer()
    data = {
        "hostile": {_TYPE_TAG: "foo", "when": datetime.datetime(2021, 5, 5)},
        "real": {"stamp": datetime.date(2020, 1, 1), "id": uuid.uuid4()},
    }
    assert codec.loads(codec.dumps(data)) == data


# --------------------------------------------------------------------------
# Finding 9: pagination must preserve blank-valued query params
# --------------------------------------------------------------------------


def test_link_headers_preserve_blank_valued_param():
    req = SimpleNamespace(full_url="http://;/items?q=&sort=name&page=2&size=10")
    resp = _stub_resp()
    set_pagination_headers(req, resp, paginate(list(range(55)), page=2, size=10))
    links = _links(resp.headers["Link"])
    # The blank-valued 'q=' must survive the URL rebuild, not be dropped.
    assert "q=&" in links["next"]
    assert "sort=name" in links["next"]
    assert "page=3" in links["next"]


# --------------------------------------------------------------------------
# Finding 10: request-ID must accept real-world correlation IDs verbatim
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "inbound",
    [b"gateway:abc123", b"req/12+34=", b"urn:uuid:abc", b"a=b/c+d:e"],
)
def test_resolve_request_id_accepts_namespaced_and_base64(inbound):
    assert _resolve_request_id({b"x-request-id": inbound}) == inbound.decode()


@pytest.mark.parametrize("config", [{"request_id": True}, {"enable_logging": True}])
def test_middlewares_echo_namespaced_inbound_id(config):
    api = _api(**config)

    @api.route("/")
    def view(req, resp):
        resp.text = "ok"

    for rid in ("gateway:abc123", "req/12+34="):
        r = api.requests.get("/", headers={"X-Request-ID": rid})
        assert r.headers["X-Request-ID"] == rid


@pytest.mark.parametrize("config", [{"request_id": True}, {"enable_logging": True}])
def test_middlewares_still_reject_crlf_injection(config):
    api = _api(**config)

    @api.route("/")
    def view(req, resp):
        resp.text = "ok"

    # An httpx client rejects raw CRLF in a header value, so validate the
    # resolver directly (the injection guard lives there).
    assert UUID4_RE.fullmatch(
        _resolve_request_id({b"x-request-id": b"legit\r\nSet-Cookie: pwned"})
    )


# --------------------------------------------------------------------------
# Finding 3: security-scheme registration must not silently overwrite
# --------------------------------------------------------------------------


def _oauth_api():
    return _api(openapi="3.0.0")


def test_add_security_scheme_rejects_conflicting_definition():
    from responder.ext.auth import OAuth2Auth

    api = _oauth_api()
    auth_code = OAuth2Auth.authorization_code(
        "https://issuer/authorize",
        "https://issuer/token",
        scopes={"read": "Read"},
        verify=lambda t: {"sub": "x"},
    )
    client_creds = OAuth2Auth.client_credentials(
        "https://issuer/token",
        scopes={"write": "Write"},
        verify=lambda t: {"sub": "x"},
    )
    # Both default to scheme_name="oauth2Auth" but declare different flows.
    assert auth_code.scheme_name == client_creds.scheme_name == "oauth2Auth"
    api.add_security_scheme(auth_code)
    with pytest.raises(ValueError, match="already registered"):
        api.add_security_scheme(client_creds)


def test_add_security_scheme_allows_idempotent_reregistration():
    from responder.ext.auth import OAuth2Auth

    api = _oauth_api()
    auth = OAuth2Auth.authorization_code(
        "https://issuer/authorize",
        "https://issuer/token",
        scopes={"read": "Read"},
        verify=lambda t: {"sub": "x"},
    )
    # route() re-registers a route's scheme every time — equal dicts must pass.
    for _ in range(3):
        api.add_security_scheme(auth)
    assert "oauth2Auth" in api.openapi.security_schemes
