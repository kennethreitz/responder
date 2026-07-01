"""Tests for conditional requests (ETag/Last-Modified), request streaming,
application state, rate-limiter backends, and static_dir=None routing."""

from datetime import datetime, timezone

import responder
from responder.ext.ratelimit import RateLimiter, RedisBackend

# --- conditional requests: ETag ---


def test_etag_match_returns_304(api):
    @api.route("/doc")
    def doc(req, resp):
        resp.etag = "v1"
        resp.text = "expensive body"

    r = api.requests.get("/doc")
    assert r.status_code == 200
    assert r.headers["ETag"] == '"v1"'
    assert r.text == "expensive body"

    r = api.requests.get("/doc", headers={"If-None-Match": '"v1"'})
    assert r.status_code == 304
    assert r.text == ""
    assert r.headers["ETag"] == '"v1"'


def test_etag_mismatch_returns_body(api):
    @api.route("/doc")
    def doc(req, resp):
        resp.etag = "v2"
        resp.text = "body"

    r = api.requests.get("/doc", headers={"If-None-Match": '"v1"'})
    assert r.status_code == 200
    assert r.text == "body"


def test_etag_star_and_multiple_tags(api):
    @api.route("/doc")
    def doc(req, resp):
        resp.etag = "abc"
        resp.text = "body"

    assert api.requests.get("/doc", headers={"If-None-Match": "*"}).status_code == 304
    r = api.requests.get("/doc", headers={"If-None-Match": '"x", "abc", "y"'})
    assert r.status_code == 304


def test_weak_etag_comparison(api):
    @api.route("/doc")
    def doc(req, resp):
        resp.etag = 'W/"weak1"'
        resp.text = "body"

    r = api.requests.get("/doc", headers={"If-None-Match": 'W/"weak1"'})
    assert r.status_code == 304
    # Weak comparison: a strong tag with the same core also matches.
    r = api.requests.get("/doc", headers={"If-None-Match": '"weak1"'})
    assert r.status_code == 304


def test_etag_ignored_for_post(api):
    @api.route("/doc", methods=["POST"])
    def doc(req, resp):
        resp.etag = "v1"
        resp.text = "created"

    r = api.requests.post("/doc", headers={"If-None-Match": '"v1"'})
    assert r.status_code == 200


# --- conditional requests: Last-Modified ---


def test_last_modified_304(api):
    stamp = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    @api.route("/page")
    def page(req, resp):
        resp.last_modified = stamp
        resp.text = "content"

    r = api.requests.get("/page")
    assert r.status_code == 200
    assert r.headers["Last-Modified"] == "Thu, 15 Jan 2026 12:00:00 GMT"

    r = api.requests.get(
        "/page", headers={"If-Modified-Since": "Thu, 15 Jan 2026 12:00:00 GMT"}
    )
    assert r.status_code == 304

    # An older If-Modified-Since gets the body.
    r = api.requests.get(
        "/page", headers={"If-Modified-Since": "Wed, 14 Jan 2026 12:00:00 GMT"}
    )
    assert r.status_code == 200


def test_if_none_match_takes_precedence(api):
    stamp = datetime(2026, 1, 15, tzinfo=timezone.utc)

    @api.route("/page")
    def page(req, resp):
        resp.etag = "current"
        resp.last_modified = stamp
        resp.text = "content"

    # ETag mismatch wins even though If-Modified-Since would match.
    r = api.requests.get(
        "/page",
        headers={
            "If-None-Match": '"stale"',
            "If-Modified-Since": "Thu, 15 Jan 2026 00:00:00 GMT",
        },
    )
    assert r.status_code == 200


# --- request body streaming ---


def test_request_stream(api):
    @api.route("/upload", methods=["POST"])
    async def upload(req, resp):
        chunks = []
        async for chunk in req.stream():
            chunks.append(chunk)
        resp.media = {"size": sum(len(c) for c in chunks)}

    payload = b"x" * 10_000
    r = api.requests.post("/upload", content=payload)
    assert r.json() == {"size": 10_000}


def test_request_stream_after_content(api):
    @api.route("/echo", methods=["POST"])
    async def echo(req, resp):
        body = await req.content
        streamed = b""
        async for chunk in req.stream():
            streamed += chunk
        resp.media = {"same": body == streamed}

    r = api.requests.post("/echo", content=b"hello")
    assert r.json() == {"same": True}


# --- application state & req.api ---


def test_app_state_reachable_from_handlers(api):
    api.state.greeting = "hi from state"

    @api.route("/")
    def view(req, resp):
        resp.text = req.api.state.greeting

    assert api.requests.get("/").text == "hi from state"


def test_req_api_is_the_api(api):
    seen = []

    @api.route("/")
    def view(req, resp):
        seen.append(req.api is api)
        resp.text = "ok"

    api.requests.get("/")
    assert seen == [True]


# --- rate limiter backends ---


def test_custom_backend(api):
    class DenyAllBackend:
        def hit(self, key, max_requests, period):
            return False, 0

    limiter = RateLimiter(requests=100, period=60, backend=DenyAllBackend())

    @api.route("/limited")
    @limiter.limit
    def limited(req, resp):
        resp.text = "never"

    assert api.requests.get("/limited").status_code == 429


class FakeRedis:
    """Minimal in-memory stand-in for redis-py's client."""

    def __init__(self):
        self.counters = {}
        self.expirations = {}

    def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def expire(self, key, seconds):
        self.expirations[key] = seconds

    def eval(self, script, numkeys, *keys_and_args):
        # Emulate the atomic INCR + first-hit EXPIRE Lua script.
        key = keys_and_args[0]
        count = self.incr(key)
        if count == 1:
            self.expire(key, keys_and_args[1])
        return count


def test_redis_backend_with_client(api):
    fake = FakeRedis()
    limiter = RateLimiter(requests=2, period=60, backend=RedisBackend(client=fake))

    @api.route("/limited")
    @limiter.limit
    def limited(req, resp):
        resp.text = "ok"

    assert api.requests.get("/limited").status_code == 200
    assert api.requests.get("/limited").status_code == 200
    assert api.requests.get("/limited").status_code == 429

    # Keys are prefixed, and expiry was set once on first hit.
    (key,) = fake.counters
    assert key.startswith("responder:ratelimit:")
    assert fake.expirations[key] == 60


def test_memory_backend_remaining_header(api):
    limiter = RateLimiter(requests=5, period=60)
    limiter.install(api)

    @api.route("/")
    def view(req, resp):
        resp.text = "ok"

    r = api.requests.get("/")
    assert r.headers["X-RateLimit-Limit"] == "5"
    assert r.headers["X-RateLimit-Remaining"] == "4"


# --- static_dir=None routing fix ---


def test_routes_work_with_static_dir_disabled():
    api = responder.API(static_dir=None, allowed_hosts=[";"])

    @api.route("/ping")
    def ping(req, resp):
        resp.text = "pong"

    assert api.requests.get("/ping").text == "pong"
