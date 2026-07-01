"""Regression tests for two reliability improvements:

- Background task pool drains in-flight work on shutdown.
- Redis rate-limit backends increment + expire atomically (no permanent
  lock-out if the process dies between the two operations).
"""

import threading

import pytest

import responder
from responder.background import BackgroundQueue
from responder.ext.ratelimit import RateLimiter, RedisBackend

# --------------------------------------------------------------------------
# Background queue graceful shutdown
# --------------------------------------------------------------------------


def test_background_shutdown_drains_in_flight_tasks():
    """shutdown(wait=True) blocks until submitted tasks finish."""
    queue = BackgroundQueue(n=2)
    done = threading.Event()
    started = threading.Event()

    def slow_task():
        started.set()
        # Long enough that the task is still running when shutdown is called.
        time_to_finish.wait(timeout=5)
        done.set()

    time_to_finish = threading.Event()
    queue.run(slow_task)
    assert started.wait(timeout=5)

    # Let the task complete, then drain.
    time_to_finish.set()
    queue.shutdown(wait=True)
    assert done.is_set()


def test_background_shutdown_rejects_new_tasks():
    """After shutdown the pool no longer accepts work."""
    queue = BackgroundQueue(n=1)
    queue.shutdown(wait=True)
    with pytest.raises(RuntimeError):
        queue.run(lambda: None)


def test_app_shutdown_triggers_background_drain():
    """The app registers a shutdown handler that drains the pool."""
    api = responder.API(allowed_hosts=[";"], session_https_only=False)

    @api.route("/")
    def index(req, resp):
        resp.text = "ok"

    # Entering/exiting the TestClient context fires startup/shutdown events.
    with api.requests as client:
        assert client.get("/").status_code == 200

    # The shutdown event drained (and closed) the background pool.
    with pytest.raises(RuntimeError):
        api.background.run(lambda: None)


# --------------------------------------------------------------------------
# Redis rate-limit atomicity
# --------------------------------------------------------------------------


class FakeRedis:
    """Minimal Redis stand-in that only understands the INCR+EXPIRE script.

    ``incr``/``expire`` raise if called directly, proving the backend routes
    through the atomic ``eval`` path rather than two separate round-trips.
    """

    def __init__(self):
        self.counts = {}
        self.expiries = {}

    def eval(self, script, numkeys, *keys_and_args):
        key = keys_and_args[0]
        period = keys_and_args[1]
        count = self.counts.get(key, 0) + 1
        self.counts[key] = count
        if count == 1:
            self.expiries[key] = period
        return count

    def incr(self, key):  # pragma: no cover - must not be called
        raise AssertionError("incr called directly; expected atomic eval")

    def expire(self, key, period):  # pragma: no cover - must not be called
        raise AssertionError("expire called directly; expected atomic eval")


def test_redis_backend_uses_atomic_eval_and_sets_expiry():
    fake = FakeRedis()
    backend = RedisBackend(client=fake)

    allowed, remaining = backend.hit("1.2.3.4", max_requests=2, period=60)
    assert (allowed, remaining) == (True, 1)
    # Expiry attached on the very first hit, atomically with the increment.
    assert fake.expiries[backend.prefix + "1.2.3.4"] == 60

    allowed, remaining = backend.hit("1.2.3.4", max_requests=2, period=60)
    assert (allowed, remaining) == (True, 0)

    allowed, remaining = backend.hit("1.2.3.4", max_requests=2, period=60)
    assert (allowed, remaining) == (False, 0)

    # Only one expiry was ever set for this window.
    assert list(fake.expiries.values()) == [60]


def test_redis_backend_end_to_end():
    api = responder.API(allowed_hosts=[";"], session_https_only=False)
    limiter = RateLimiter(requests=2, period=60, backend=RedisBackend(client=FakeRedis()))
    limiter.install(api)

    @api.route("/")
    def view(req, resp):
        resp.text = "ok"

    client = api.requests
    assert client.get("/").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/").status_code == 429
