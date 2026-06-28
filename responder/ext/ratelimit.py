"""Rate limiting for Responder, with pluggable storage backends."""

import functools
import inspect
import threading
import time
from collections import defaultdict
from typing import Protocol, runtime_checkable

from starlette.concurrency import run_in_threadpool


@runtime_checkable
class RateLimitBackend(Protocol):
    """A synchronous rate-limit store."""

    def hit(self, key: str, max_requests: int, period: int) -> tuple[bool, int]: ...


@runtime_checkable
class AsyncRateLimitBackend(Protocol):
    """An async-native rate-limit store (awaited directly, no thread)."""

    async def ahit(
        self, key: str, max_requests: int, period: int
    ) -> tuple[bool, int]: ...


class MemoryBackend:
    """Sliding-window backend storing hit timestamps in process memory.

    The default backend. Counts are per-process — for multi-process or
    multi-host deployments, use :class:`RedisBackend` instead.
    """

    def __init__(self):
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def hit(self, key, max_requests, period):
        """Record a hit for ``key``. Returns ``(allowed, remaining)``."""
        now = time.time()
        cutoff = now - period

        with self._lock:
            bucket = [t for t in self._buckets[key] if t > cutoff]
            if len(bucket) >= max_requests:
                self._buckets[key] = bucket
                return False, 0
            bucket.append(now)
            self._buckets[key] = bucket
            return True, max_requests - len(bucket)


class RedisBackend:
    """Fixed-window backend backed by Redis, shared across processes.

    Pass an existing client, or a ``url`` to create one (requires the
    ``redis`` package)::

        from responder.ext.ratelimit import RateLimiter, RedisBackend

        limiter = RateLimiter(
            requests=100, period=60,
            backend=RedisBackend(url="redis://localhost:6379/0"),
        )

    """

    def __init__(self, client=None, *, url=None, prefix="responder:ratelimit:"):
        if client is None:
            try:
                import redis
            except ImportError as exc:
                raise ImportError(
                    "redis is required for RedisBackend: pip install redis"
                ) from exc
            client = redis.Redis.from_url(url or "redis://localhost:6379/0")
        self.client = client
        self.prefix = prefix

    def hit(self, key, max_requests, period):
        """Record a hit for ``key``. Returns ``(allowed, remaining)``."""
        redis_key = self.prefix + key
        count = self.client.incr(redis_key)
        if count == 1:
            self.client.expire(redis_key, period)
        if count > max_requests:
            return False, 0
        return True, max_requests - count


class AsyncRedisBackend:
    """Async-native fixed-window Redis backend (uses ``redis.asyncio``)."""

    def __init__(self, client=None, *, url=None, prefix="responder:ratelimit:"):
        if client is None:
            try:
                from redis import asyncio as aioredis
            except ImportError as exc:
                raise ImportError(
                    "redis is required for AsyncRedisBackend: pip install redis"
                ) from exc
            client = aioredis.Redis.from_url(url or "redis://localhost:6379/0")
        self.client = client
        self.prefix = prefix

    async def ahit(self, key, max_requests, period):
        redis_key = self.prefix + key
        count = await self.client.incr(redis_key)
        if count == 1:
            await self.client.expire(redis_key, period)
        if count > max_requests:
            return False, 0
        return True, max_requests - count


class RateLimiter:
    """Token bucket rate limiter.

    Usage::

        from responder.ext.ratelimit import RateLimiter

        limiter = RateLimiter(requests=100, period=60)  # 100 req/min

        @api.route(before_request=True)
        def rate_limit(req, resp):
            limiter.check(req, resp)

    Or use the shorthand::

        limiter = RateLimiter(requests=100, period=60)
        limiter.install(api)

    To rate-limit a single route, apply :meth:`limit` beneath ``@api.route``.
    Give each route its own ``RateLimiter`` for an independent budget::

        expensive_limiter = RateLimiter(requests=5, period=60)

        @api.route("/expensive")
        @expensive_limiter.limit
        async def expensive(req, resp):
            ...

    """

    def __init__(self, requests=100, period=60, backend=None):
        """Create a rate limiter.

        :param requests: Maximum requests allowed per ``period``.
        :param period: The window length, in seconds.
        :param backend: Storage backend (defaults to :class:`MemoryBackend`).
                        Any object with a
                        ``hit(key, max_requests, period) -> (allowed, remaining)``
                        method works, e.g. :class:`RedisBackend`.
        """
        self.max_requests = requests
        self.period = period
        self.backend = MemoryBackend() if backend is None else backend

    def _client_key(self, req):
        client = req.client
        if client:
            return client[0]
        return req.headers.get("X-Forwarded-For", "unknown")

    def _apply(self, allowed, remaining, resp):
        if not allowed:
            resp.status_code = 429
            resp.media = {"error": "rate limit exceeded"}
            resp.headers["Retry-After"] = str(self.period)
            return False
        resp.headers["X-RateLimit-Limit"] = str(self.max_requests)
        resp.headers["X-RateLimit-Remaining"] = str(remaining)
        return True

    def check(self, req, resp):
        """Check the rate limit synchronously. Sets a ``429`` if exceeded.

        Requires a sync backend (``hit``); use :meth:`acheck` for async-only
        backends.
        """
        allowed, remaining = self.backend.hit(
            self._client_key(req), self.max_requests, self.period
        )
        return self._apply(allowed, remaining, resp)

    async def acheck(self, req, resp):
        """Check the rate limit, awaiting an async backend (or off-loading a
        sync one to a thread). Works with any backend."""
        key = self._client_key(req)
        if hasattr(self.backend, "ahit"):
            allowed, remaining = await self.backend.ahit(
                key, self.max_requests, self.period
            )
        else:
            allowed, remaining = await run_in_threadpool(
                self.backend.hit, key, self.max_requests, self.period
            )
        return self._apply(allowed, remaining, resp)

    def limit(self, f):
        """Decorator that rate-limits a single route handler.

        Apply beneath ``@api.route()``. When the limit is exceeded, the
        handler is skipped and a 429 response is returned.
        """
        if inspect.iscoroutinefunction(f):

            @functools.wraps(f)
            async def wrapper(req, resp, *args, **kwargs):
                if await self.acheck(req, resp):
                    await f(req, resp, *args, **kwargs)

        else:

            @functools.wraps(f)
            def wrapper(req, resp, *args, **kwargs):
                if self.check(req, resp):
                    f(req, resp, *args, **kwargs)

        return wrapper

    def install(self, api):
        """Install as a before_request hook on the API (async, any backend)."""

        @api.route(before_request=True)
        async def _rate_limit(req, resp):
            await self.acheck(req, resp)
