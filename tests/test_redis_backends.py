"""Real coverage for the Redis-backed session and rate-limit backends.

Earlier suites exercised these classes only through hand-rolled stub
clients; here `fakeredis` (sync + async, with Lua support via `lupa`)
runs the actual commands — including the atomic INCR+EXPIRE Lua script —
against an in-memory Redis emulation, plus its connection-error
simulation for outage behavior.
"""

import asyncio
import json
import sys
import time

import pytest

import responder
from responder.ext.ratelimit import AsyncRedisBackend, RateLimiter, RedisBackend
from responder.ext.sessions import AsyncRedisSessionBackend, RedisSessionBackend

try:
    import fakeredis
    import fakeredis.aioredis
    import redis.exceptions  # fakeredis depends on redis-py
except ImportError:  # pragma: no cover - exercised only without fakeredis
    fakeredis = None

pytestmark = pytest.mark.skipif(fakeredis is None, reason="fakeredis not installed")

try:
    import lupa  # noqa: F401

    HAS_LUA = True
except ImportError:  # pragma: no cover - exercised only without lupa
    HAS_LUA = False

needs_lua = pytest.mark.skipif(
    not HAS_LUA, reason="fakeredis Lua (EVAL) support requires lupa"
)


def make_client():
    return fakeredis.FakeRedis()


def make_down_client():
    """A client whose every command raises redis.exceptions.ConnectionError."""
    server = fakeredis.FakeServer()
    server.connected = False
    return fakeredis.FakeRedis(server=server)


def make_async_down_client():
    server = fakeredis.FakeServer()
    server.connected = False
    return fakeredis.aioredis.FakeRedis(server=server)


def make_api(**kwargs):
    return responder.API(allowed_hosts=[";"], session_https_only=False, **kwargs)


# --------------------------------------------------------------------------
# RedisSessionBackend (sync)
# --------------------------------------------------------------------------


def test_redis_session_set_get_delete_round_trip():
    fake = make_client()
    backend = RedisSessionBackend(client=fake)

    assert backend.get("missing") is None

    backend.set("sid", {"user": "kenneth", "n": 3}, max_age=60)
    assert backend.get("sid") == {"user": "kenneth", "n": 3}

    backend.delete("sid")
    assert backend.get("sid") is None
    # Deleting a missing session is a no-op, not an error.
    backend.delete("sid")


def test_redis_session_set_uses_set_with_expiry_instead_of_deprecated_setex():
    class FakeRedis:
        def __init__(self):
            self.calls = []
            self.store = {}

        def set(self, key, value, *, ex):
            self.calls.append(("set", key, value, ex))
            self.store[key] = value

        def setex(self, key, ttl, value):  # pragma: no cover - must not be called
            self.calls.append(("setex", key, value, ttl))

        def get(self, key):
            return self.store.get(key)

    fake = FakeRedis()
    backend = RedisSessionBackend(client=fake)

    backend.set("sid", {"user": "kenneth"}, max_age=60)

    assert fake.calls == [
        ("set", "responder:session:sid", '{"user": "kenneth"}', 60)
    ]
    assert backend.get("sid") == {"user": "kenneth"}


def test_redis_session_prefix():
    fake = make_client()

    RedisSessionBackend(client=fake).set("sid", {"a": 1}, max_age=60)
    assert fake.exists("responder:session:sid")

    RedisSessionBackend(client=fake, prefix="myapp:").set("sid2", {"b": 2}, max_age=60)
    assert fake.exists("myapp:sid2")
    # Prefixes isolate: the custom-prefix backend can't see the default's key.
    assert RedisSessionBackend(client=fake, prefix="myapp:").get("sid") is None


def test_redis_session_ttl_set_and_touch():
    fake = make_client()
    backend = RedisSessionBackend(client=fake)

    backend.set("sid", {"user": "k"}, max_age=100)
    assert 0 < fake.ttl("responder:session:sid") <= 100

    # touch slides the TTL without rewriting the payload.
    backend.touch("sid", 500)
    assert 100 < fake.ttl("responder:session:sid") <= 500
    assert backend.get("sid") == {"user": "k"}

    # Touching a missing session is a no-op.
    backend.touch("missing", 500)
    assert backend.get("missing") is None


def test_redis_session_expiry():
    fake = make_client()
    backend = RedisSessionBackend(client=fake)

    backend.set("sid", {"user": "k"}, max_age=1)
    assert backend.get("sid") == {"user": "k"}
    time.sleep(1.1)
    assert backend.get("sid") is None


def test_redis_session_serializer_round_trips_common_types():
    """8.1 contract: the default JSONSessionSerializer round-trips the
    documented common types (datetime, date, set, Decimal, ...); tuples
    still come back as lists (a JSON limitation).
    """
    fake = make_client()
    backend = RedisSessionBackend(client=fake)

    backend.set("sid", {"pair": (1, 2)}, max_age=60)
    assert backend.get("sid") == {"pair": [1, 2]}

    import datetime
    from decimal import Decimal

    when = datetime.datetime(2026, 7, 3, 12, 0, tzinfo=datetime.timezone.utc)
    backend.set(
        "sid2",
        {"when": when, "tags": {"a", "b"}, "price": Decimal("9.99")},
        max_age=60,
    )
    restored = backend.get("sid2")
    assert restored["when"] == when
    assert restored["tags"] == {"a", "b"}
    assert restored["price"] == Decimal("9.99")


def test_redis_session_middleware_end_to_end():
    fake = make_client()
    api = make_api(session_backend=RedisSessionBackend(client=fake))

    @api.route("/login", methods=["POST"])
    async def login(req, resp):
        req.session["user"] = "kenneth"
        resp.media = {"ok": True}

    @api.route("/whoami")
    async def whoami(req, resp):
        resp.media = {"user": req.session.get("user")}

    @api.route("/logout", methods=["POST"])
    async def logout(req, resp):
        req.session.clear()
        resp.media = {"ok": True}

    client = api.requests
    assert client.get("/whoami").json() == {"user": None}

    r = client.post("/login")
    assert "responder_session=" in r.headers["set-cookie"]
    assert client.get("/whoami").json() == {"user": "kenneth"}

    # Data lives in Redis under the prefix, and the backend TTL is set.
    (key,) = fake.keys("responder:session:*")
    assert json.loads(fake.get(key)) == {"user": "kenneth"}
    assert fake.ttl(key) > 0

    r = client.post("/logout")
    assert "Max-Age=0" in r.headers["set-cookie"]
    assert client.get("/whoami").json() == {"user": None}
    assert fake.keys("responder:session:*") == []


def test_redis_session_connection_error_propagates():
    """Current contract: a Redis outage propagates ConnectionError (fail-closed)."""
    backend = RedisSessionBackend(client=make_down_client())

    with pytest.raises(redis.exceptions.ConnectionError):
        backend.get("sid")
    with pytest.raises(redis.exceptions.ConnectionError):
        backend.set("sid", {"a": 1}, max_age=60)
    with pytest.raises(redis.exceptions.ConnectionError):
        backend.delete("sid")
    with pytest.raises(redis.exceptions.ConnectionError):
        backend.touch("sid", 60)


def test_redis_session_outage_yields_500_for_cookie_bearing_requests():
    """End-to-end: with Redis down, a request presenting a session cookie 500s
    before the handler runs (current fail-closed contract)."""
    from starlette.testclient import TestClient

    api = make_api(session_backend=RedisSessionBackend(client=make_down_client()))

    @api.route("/")
    async def index(req, resp):
        resp.text = "ok"

    client = TestClient(api, base_url="http://;", raise_server_exceptions=False)
    # No cookie -> the backend is never consulted on the way in, and an
    # untouched (empty) session is never written, so the request succeeds.
    assert client.get("/").status_code == 200
    # A presented cookie forces a backend read, which fails.
    client.cookies.set("responder_session", "whatever")
    r = client.get("/")
    assert r.status_code == 500


# --------------------------------------------------------------------------
# AsyncRedisSessionBackend
# --------------------------------------------------------------------------


def test_async_redis_session_round_trip():
    fake = fakeredis.aioredis.FakeRedis()
    backend = AsyncRedisSessionBackend(client=fake)

    async def scenario():
        assert await backend.aget("missing") is None

        await backend.aset("sid", {"user": "kenneth"}, max_age=100)
        assert await backend.aget("sid") == {"user": "kenneth"}
        assert 0 < await fake.ttl("responder:session:sid") <= 100

        await backend.atouch("sid", 500)
        assert 100 < await fake.ttl("responder:session:sid") <= 500

        await backend.adelete("sid")
        assert await backend.aget("sid") is None

    asyncio.run(scenario())


def test_async_redis_session_set_uses_set_with_expiry_instead_of_deprecated_setex():
    class FakeAsyncRedis:
        def __init__(self):
            self.calls = []
            self.store = {}

        async def set(self, key, value, *, ex):
            self.calls.append(("set", key, value, ex))
            self.store[key] = value

        async def setex(self, key, ttl, value):  # pragma: no cover - must not be called
            self.calls.append(("setex", key, value, ttl))

        async def get(self, key):
            return self.store.get(key)

    fake = FakeAsyncRedis()
    backend = AsyncRedisSessionBackend(client=fake)

    async def scenario():
        await backend.aset("sid", {"user": "kenneth"}, max_age=60)
        assert await backend.aget("sid") == {"user": "kenneth"}

    asyncio.run(scenario())

    assert fake.calls == [
        ("set", "responder:session:sid", '{"user": "kenneth"}', 60)
    ]


def test_async_redis_session_prefix():
    fake = fakeredis.aioredis.FakeRedis()
    backend = AsyncRedisSessionBackend(client=fake, prefix="myapp:")

    async def scenario():
        await backend.aset("sid", {"a": 1}, max_age=60)
        assert await fake.exists("myapp:sid")
        assert not await fake.exists("responder:session:sid")

    asyncio.run(scenario())


def test_async_redis_session_middleware_end_to_end():
    fake = fakeredis.aioredis.FakeRedis()
    api = make_api(session_backend=AsyncRedisSessionBackend(client=fake))

    @api.route("/login", methods=["POST"])
    async def login(req, resp):
        req.session["cart"] = [1, 2, 3]
        resp.media = {"ok": True}

    @api.route("/cart")
    async def cart(req, resp):
        resp.media = {"cart": req.session.get("cart")}

    client = api.requests
    client.post("/login")
    assert client.get("/cart").json() == {"cart": [1, 2, 3]}


def test_async_redis_session_connection_error_propagates():
    backend = AsyncRedisSessionBackend(client=make_async_down_client())

    async def scenario():
        with pytest.raises(redis.exceptions.ConnectionError):
            await backend.aget("sid")
        with pytest.raises(redis.exceptions.ConnectionError):
            await backend.aset("sid", {"a": 1}, max_age=60)
        with pytest.raises(redis.exceptions.ConnectionError):
            await backend.adelete("sid")
        with pytest.raises(redis.exceptions.ConnectionError):
            await backend.atouch("sid", 60)

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# Rate-limit RedisBackend (sync) — exercises the real Lua script
# --------------------------------------------------------------------------


def _hit(backend, key, max_requests, period):
    """Unpack a backend hit as (allowed, remaining, reset_after).

    Backends return ``(allowed, remaining, reset_after)``; tolerate the
    legacy 2-tuple form (``reset_after=None``) so these tests track the
    documented backend contract rather than one specific arity.
    """
    result = backend.hit(key, max_requests, period)
    if len(result) == 2:
        return (*result, None)
    return result


@needs_lua
def test_redis_ratelimit_hit_counting():
    backend = RedisBackend(client=make_client())

    assert _hit(backend, "1.2.3.4", 3, 60)[:2] == (True, 2)
    assert _hit(backend, "1.2.3.4", 3, 60)[:2] == (True, 1)
    assert _hit(backend, "1.2.3.4", 3, 60)[:2] == (True, 0)
    assert _hit(backend, "1.2.3.4", 3, 60)[:2] == (False, 0)
    # Denied hits keep counting: still denied.
    assert _hit(backend, "1.2.3.4", 3, 60)[:2] == (False, 0)
    # Keys are independent.
    assert _hit(backend, "5.6.7.8", 3, 60)[:2] == (True, 2)


@needs_lua
def test_redis_ratelimit_reports_reset_after():
    """The third element of hit() is the window's remaining TTL, in seconds."""
    backend = RedisBackend(client=make_client())

    allowed, _, reset_after = _hit(backend, "1.2.3.4", 2, 60)
    assert allowed
    assert 0 < reset_after <= 60

    # A denied hit still reports when the window resets.
    _hit(backend, "1.2.3.4", 1, 60)
    allowed, _, reset_after = _hit(backend, "1.2.3.4", 1, 60)
    assert not allowed
    assert 0 < reset_after <= 60


@needs_lua
def test_redis_ratelimit_first_hit_sets_ttl_and_later_hits_keep_it():
    fake = make_client()
    backend = RedisBackend(client=fake)
    key = "responder:ratelimit:1.2.3.4"

    backend.hit("1.2.3.4", max_requests=10, period=60)
    assert 0 < fake.ttl(key) <= 60

    # Shrink the TTL out-of-band; a subsequent hit must NOT re-arm it —
    # the Lua script attaches EXPIRE only when the counter is created.
    fake.expire(key, 5)
    backend.hit("1.2.3.4", max_requests=10, period=60)
    assert fake.ttl(key) <= 5


@needs_lua
def test_redis_ratelimit_window_reset():
    fake = make_client()
    backend = RedisBackend(client=fake)

    assert _hit(backend, "1.2.3.4", 1, 1)[:2] == (True, 0)
    assert _hit(backend, "1.2.3.4", 1, 1)[:2] == (False, 0)
    time.sleep(1.1)
    # The key expired with the window: the budget is fresh again.
    assert _hit(backend, "1.2.3.4", 1, 1)[:2] == (True, 0)


@needs_lua
def test_redis_ratelimit_prefix():
    fake = make_client()
    RedisBackend(client=fake, prefix="myapp:rl:").hit("k", max_requests=5, period=60)
    assert fake.exists("myapp:rl:k")

    RedisBackend(client=fake).hit("k", max_requests=5, period=60)
    assert fake.exists("responder:ratelimit:k")


@needs_lua
def test_redis_ratelimit_end_to_end():
    api = make_api()
    limiter = RateLimiter(requests=2, period=60, backend=RedisBackend(client=make_client()))
    limiter.install(api)

    @api.route("/")
    async def index(req, resp):
        resp.text = "ok"

    client = api.requests
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["X-RateLimit-Limit"] == "2"
    assert r.headers["X-RateLimit-Remaining"] == "1"
    # The Redis window TTL surfaces as X-RateLimit-Reset.
    assert 0 < int(r.headers["X-RateLimit-Reset"]) <= 60

    assert client.get("/").status_code == 200

    r = client.get("/")
    assert r.status_code == 429
    assert r.headers["Retry-After"] == "60"
    assert 0 < int(r.headers["X-RateLimit-Reset"]) <= 60
    body = r.json()
    assert body["title"] == "Too Many Requests"
    assert body["detail"] == "Rate limit exceeded."


def test_redis_ratelimit_connection_error_propagates():
    """Current contract: a Redis outage propagates from hit() (fail-closed)."""
    backend = RedisBackend(client=make_down_client())

    with pytest.raises(redis.exceptions.ConnectionError):
        backend.hit("1.2.3.4", max_requests=5, period=60)


def test_redis_ratelimit_outage_fails_closed_with_503():
    """End-to-end: with Redis down and fail_open=False (the default), every
    rate-limited request answers 503 rather than slipping past the limit."""
    api = make_api()
    limiter = RateLimiter(
        requests=5, period=60, backend=RedisBackend(client=make_down_client())
    )
    limiter.install(api)

    @api.route("/")
    async def index(req, resp):
        resp.text = "never"

    r = api.requests.get("/")
    assert r.status_code == 503
    assert r.headers["Retry-After"] == "60"
    body = r.json()
    assert body["title"] == "Service Unavailable"
    assert body["detail"] == "Rate limit backend unavailable."


def test_redis_ratelimit_outage_fail_open_allows_requests():
    """End-to-end: with Redis down and fail_open=True, requests pass unlimited."""
    api = make_api()
    limiter = RateLimiter(
        requests=1,
        period=60,
        backend=RedisBackend(client=make_down_client()),
        fail_open=True,
    )
    limiter.install(api)

    @api.route("/")
    async def index(req, resp):
        resp.text = "ok"

    client = api.requests
    assert client.get("/").status_code == 200
    assert client.get("/").status_code == 200  # over the nominal budget, still up


# --------------------------------------------------------------------------
# Rate-limit AsyncRedisBackend
# --------------------------------------------------------------------------


@needs_lua
def test_async_redis_ratelimit_hit_counting_and_ttl():
    fake = fakeredis.aioredis.FakeRedis()
    backend = AsyncRedisBackend(client=fake)

    async def ahit(key, max_requests, period):
        result = await backend.ahit(key, max_requests, period)
        if len(result) == 2:
            return (*result, None)
        return result

    async def scenario():
        assert (await ahit("1.2.3.4", 2, 60))[:2] == (True, 1)
        assert 0 < await fake.ttl("responder:ratelimit:1.2.3.4") <= 60
        assert (await ahit("1.2.3.4", 2, 60))[:2] == (True, 0)
        allowed, remaining, reset_after = await ahit("1.2.3.4", 2, 60)
        assert (allowed, remaining) == (False, 0)
        if reset_after is not None:
            assert 0 < reset_after <= 60

    asyncio.run(scenario())


@needs_lua
def test_async_redis_ratelimit_end_to_end():
    api = make_api()
    limiter = RateLimiter(
        requests=1, period=60, backend=AsyncRedisBackend(client=fakeredis.aioredis.FakeRedis())
    )
    limiter.install(api)

    @api.route("/")
    async def index(req, resp):
        resp.text = "ok"

    client = api.requests
    assert client.get("/").status_code == 200
    assert client.get("/").status_code == 429


def test_async_redis_ratelimit_connection_error_propagates():
    backend = AsyncRedisBackend(client=make_async_down_client())

    async def scenario():
        with pytest.raises(redis.exceptions.ConnectionError):
            await backend.ahit("1.2.3.4", max_requests=5, period=60)

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# Missing-redis ImportError contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cls",
    [RedisSessionBackend, AsyncRedisSessionBackend, RedisBackend, AsyncRedisBackend],
)
def test_backends_raise_import_error_without_redis(monkeypatch, cls):
    """Constructing without a client requires the redis package; the error
    tells the user how to fix it."""
    monkeypatch.setitem(sys.modules, "redis", None)  # makes `import redis` fail
    with pytest.raises(ImportError, match="pip install redis"):
        cls()


@pytest.mark.parametrize(
    "cls",
    [RedisSessionBackend, AsyncRedisSessionBackend, RedisBackend, AsyncRedisBackend],
)
def test_backends_build_client_from_url(cls):
    """A url= (no client) builds a redis client without connecting."""
    backend = cls(url="redis://localhost:1/0")
    assert backend.client is not None
    assert backend.prefix.endswith(":")
