"""Simple in-memory rate limiter for Responder."""

import functools
import inspect
import threading
import time
from collections import defaultdict


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

    def __init__(self, requests=100, period=60):
        self.max_requests = requests
        self.period = period
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def _client_key(self, req):
        client = req.client
        if client:
            return client[0]
        return req.headers.get("X-Forwarded-For", "unknown")

    def _cleanup(self, key):
        now = time.time()
        cutoff = now - self.period
        self._buckets[key] = [t for t in self._buckets[key] if t > cutoff]
        if not self._buckets[key]:
            del self._buckets[key]

    def check(self, req, resp):
        """Check rate limit. Sets 429 status if exceeded."""
        key = self._client_key(req)

        with self._lock:
            self._cleanup(key)

            if len(self._buckets[key]) >= self.max_requests:
                resp.status_code = 429
                resp.media = {"error": "rate limit exceeded"}
                resp.headers["Retry-After"] = str(self.period)
                return False

            self._buckets[key].append(time.time())
            remaining = self.max_requests - len(self._buckets[key])

        resp.headers["X-RateLimit-Limit"] = str(self.max_requests)
        resp.headers["X-RateLimit-Remaining"] = str(remaining)
        return True

    def limit(self, f):
        """Decorator that rate-limits a single route handler.

        Apply beneath ``@api.route()``. When the limit is exceeded, the
        handler is skipped and a 429 response is returned.
        """
        if inspect.iscoroutinefunction(f):

            @functools.wraps(f)
            async def wrapper(req, resp, *args, **kwargs):
                if self.check(req, resp):
                    await f(req, resp, *args, **kwargs)

        else:

            @functools.wraps(f)
            def wrapper(req, resp, *args, **kwargs):
                if self.check(req, resp):
                    f(req, resp, *args, **kwargs)

        return wrapper

    def install(self, api):
        """Install as a before_request hook on the API."""

        @api.route(before_request=True)
        def _rate_limit(req, resp):
            self.check(req, resp)
