"""Rate limiting for Responder, with pluggable storage backends."""

import functools
import inspect
import logging
import math
import threading
import time
from collections import OrderedDict
from typing import Protocol, runtime_checkable

from starlette.concurrency import run_in_threadpool

from ..util.net import resolve_client_ip

logger = logging.getLogger("responder")


@runtime_checkable
class RateLimitBackend(Protocol):
    """A synchronous rate-limit store.

    ``hit`` may return either ``(allowed, remaining)`` or — preferred —
    ``(allowed, remaining, reset_after)`` where ``reset_after`` is the
    number of seconds until the client's window resets (used for the
    ``X-RateLimit-Reset`` response header). Two-tuple backends keep
    working; the header is simply omitted.
    """

    def hit(self, key: str, max_requests: int, period: int) -> tuple[bool, int]: ...


@runtime_checkable
class AsyncRateLimitBackend(Protocol):
    """An async-native rate-limit store (awaited directly, no thread).

    Like :class:`RateLimitBackend`, ``ahit`` may return a 2-tuple
    ``(allowed, remaining)`` or a 3-tuple ``(allowed, remaining,
    reset_after)``.
    """

    async def ahit(
        self, key: str, max_requests: int, period: int
    ) -> tuple[bool, int]: ...


class MemoryBackend:
    """Sliding-window backend storing hit timestamps in process memory.

    The default backend. Counts are per-process — for multi-process or
    multi-host deployments, use :class:`RedisBackend` instead.

    Keys are bounded: at most ``max_keys`` distinct clients are tracked, with
    least-recently-seen keys evicted beyond the cap. This stops an attacker who
    rotates source IPs (or spoofs ``X-Forwarded-For``) from growing process
    memory without bound. Evicting a key resets its window (fail-open), which is
    acceptable for an in-memory single-process limiter.
    """

    def __init__(self, max_keys: int = 100_000):
        self._buckets: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_keys = max_keys

    def hit(self, key, max_requests, period):
        """Record a hit for ``key``.

        Returns ``(allowed, remaining, reset_after)``, where ``reset_after``
        is the number of seconds until the oldest hit in the window expires.
        """
        now = time.time()
        cutoff = now - period

        with self._lock:
            bucket = [t for t in self._buckets.get(key, ()) if t > cutoff]
            self._buckets[key] = bucket
            self._buckets.move_to_end(key)  # mark most-recently-used
            if len(bucket) >= max_requests:
                reset_after = max(bucket[0] + period - now, 0.0)
                return False, 0, reset_after
            bucket.append(now)
            # Evict least-recently-used keys once over the cap.
            while self._max_keys is not None and len(self._buckets) > self._max_keys:
                self._buckets.popitem(last=False)
            reset_after = max(bucket[0] + period - now, 0.0)
            return True, max_requests - len(bucket), reset_after


# Atomic fixed-window increment: bump the counter and, on the first hit of a
# window, attach the TTL — in a single server-side step. Doing INCR and EXPIRE
# as separate calls risks the key never expiring (and thus locking a client out
# permanently) if the process dies between them. Also reads the remaining TTL
# in the same step so `hit` can report when the window resets.
_INCR_EXPIRE_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
    ttl = tonumber(ARGV[1])
end
return {count, ttl}
"""


def _eval_result(result, period):
    """Normalize the Lua script's ``{count, ttl}`` reply.

    Tolerates a bare count (an int) from fakes/proxies that emulate the
    pre-8.1 script, falling back to ``period`` as the reset time.
    """
    if isinstance(result, (list, tuple)):
        count, ttl = result
        return int(count), int(ttl)
    return int(result), period


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
        """Record a hit for ``key``.

        Returns ``(allowed, remaining, reset_after)``, where ``reset_after``
        is the remaining TTL of the current fixed window, in seconds.
        """
        redis_key = self.prefix + key
        result = self.client.eval(_INCR_EXPIRE_LUA, 1, redis_key, period)
        count, ttl = _eval_result(result, period)
        if count > max_requests:
            return False, 0, ttl
        return True, max_requests - count, ttl


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
        result = await self.client.eval(_INCR_EXPIRE_LUA, 1, redis_key, period)
        count, ttl = _eval_result(result, period)
        if count > max_requests:
            return False, 0, ttl
        return True, max_requests - count, ttl


class RateLimiter:
    """Per-client request rate limiter.

    The limiting algorithm depends on the backend: :class:`MemoryBackend`
    (the default) is a sliding window over the last ``period`` seconds,
    while :class:`RedisBackend`/:class:`AsyncRedisBackend` count within
    fixed windows.

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

    def __init__(
        self,
        requests=100,
        period=60,
        backend=None,
        trust_proxy_headers=False,
        key=None,
        fail_open=False,
    ):
        """Create a rate limiter.

        :param requests: Maximum requests allowed per ``period``.
        :param period: The window length, in seconds.
        :param backend: Storage backend (defaults to :class:`MemoryBackend`).
                        Any object with a
                        ``hit(key, max_requests, period)`` method returning
                        ``(allowed, remaining)`` or ``(allowed, remaining,
                        reset_after)`` works, e.g. :class:`RedisBackend`.
        :param trust_proxy_headers: If ``True``, key by the proxy's
                        forwarding headers instead of the TCP peer — RFC 7239
                        ``Forwarded``, then ``X-Forwarded-For``, then
                        ``X-Real-IP``, matching ``ProxyHeadersMiddleware``
                        and access logging. Set this only
                        when Responder sits behind a reverse proxy that sets
                        those headers itself — behind a proxy, every request's
                        peer is the proxy, so without this every client shares
                        one rate-limit bucket; with it unset and no proxy in
                        front, a client could otherwise spoof the header to
                        dodge the limit.
        :param key: Optional callable ``req -> str`` that names the bucket a
                        request counts against — e.g. an API key
                        (``lambda req: req.headers.get("x-api-key", "anon")``)
                        or an authenticated user id. Defaults to the client
                        IP. A returned ``None`` (or empty string) falls back
                        to the IP-based key.
        :param fail_open: What to do when the backend errors out (e.g. Redis
                        is unreachable). ``False`` (the default) answers
                        ``503 Service Unavailable`` — no request slips past
                        the limit, but an outage takes rate-limited routes
                        down with it. ``True`` logs a warning and lets the
                        request through unlimited — the app stays up, at the
                        cost of unmetered traffic while the backend is down.
        """
        self.max_requests = requests
        self.period = period
        self.backend = MemoryBackend() if backend is None else backend
        self.trust_proxy_headers = trust_proxy_headers
        self.key = key
        self.fail_open = fail_open

    def _client_key(self, req):
        if self.key is not None:
            custom = self.key(req)
            if custom:
                return str(custom)
        def get_header(name):
            # Join repeated lines (each proxy hop may append its own
            # Forwarded/X-Forwarded-For) so the resolver sees the full list
            # in order; req.headers.get would keep only the last line.
            values = req.headers.get_list(name)
            return ", ".join(values) if values else None

        ip = resolve_client_ip(
            req.client, get_header, trust_proxy_headers=self.trust_proxy_headers
        )
        return ip or "unknown"

    @staticmethod
    def _normalize(result):
        """Accept 2-tuple ``(allowed, remaining)`` or 3-tuple backends."""
        allowed, remaining, *rest = result
        reset_after = rest[0] if rest else None
        return allowed, remaining, reset_after

    def _apply(self, allowed, remaining, reset_after, resp):
        if reset_after is not None:
            resp.headers["X-RateLimit-Reset"] = str(math.ceil(reset_after))
        if not allowed:
            resp.status_code = 429
            resp.media = {"error": "rate limit exceeded"}
            resp.headers["Retry-After"] = str(self.period)
            return False
        resp.headers["X-RateLimit-Limit"] = str(self.max_requests)
        resp.headers["X-RateLimit-Remaining"] = str(remaining)
        return True

    def _apply_failure(self, exc, resp):
        if self.fail_open:
            logger.warning(
                "Rate-limit backend %s failed (%s: %s); fail_open=True, "
                "allowing request unlimited.",
                type(self.backend).__name__,
                type(exc).__name__,
                exc,
            )
            return True
        logger.error(
            "Rate-limit backend %s failed (%s: %s); fail_open=False, "
            "answering 503.",
            type(self.backend).__name__,
            type(exc).__name__,
            exc,
        )
        resp.status_code = 503
        resp.media = {"error": "rate limit backend unavailable"}
        resp.headers["Retry-After"] = str(self.period)
        return False

    def check(self, req, resp):
        """Check the rate limit synchronously. Sets a ``429`` if exceeded.

        Requires a sync backend (``hit``); use :meth:`acheck` for async-only
        backends.
        """
        key = self._client_key(req)
        try:
            result = self.backend.hit(key, self.max_requests, self.period)
        except Exception as exc:
            return self._apply_failure(exc, resp)
        allowed, remaining, reset_after = self._normalize(result)
        return self._apply(allowed, remaining, reset_after, resp)

    async def acheck(self, req, resp):
        """Check the rate limit, awaiting an async backend (or off-loading a
        sync one to a thread). Works with any backend."""
        key = self._client_key(req)
        try:
            if hasattr(self.backend, "ahit"):
                result = await self.backend.ahit(key, self.max_requests, self.period)
            else:
                result = await run_in_threadpool(
                    self.backend.hit, key, self.max_requests, self.period
                )
        except Exception as exc:
            return self._apply_failure(exc, resp)
        allowed, remaining, reset_after = self._normalize(result)
        return self._apply(allowed, remaining, reset_after, resp)

    def limit(self, f):
        """Decorator that rate-limits a single route handler.

        Apply beneath ``@api.route()``. When the limit is exceeded, the
        handler is skipped and a 429 response is returned.
        """
        if inspect.iscoroutinefunction(f):

            @functools.wraps(f)
            async def wrapper(req, resp, *args, **kwargs):
                # Propagate the handler's return value so return-value-style
                # handlers (return dict/str/bytes, or (data, status[, headers]))
                # compose with @limit. When over the limit, acheck has already
                # mutated resp to a 429 and we return None, leaving it intact.
                if await self.acheck(req, resp):
                    return await f(req, resp, *args, **kwargs)
                return None

        else:

            @functools.wraps(f)
            def wrapper(req, resp, *args, **kwargs):
                if self.check(req, resp):
                    return f(req, resp, *args, **kwargs)
                return None

        return wrapper

    def install(self, api):
        """Install as a before_request hook on the API (async, any backend)."""

        @api.route(before_request=True)
        async def _rate_limit(req, resp):
            await self.acheck(req, resp)
