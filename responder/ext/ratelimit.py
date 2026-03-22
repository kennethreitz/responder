"""Simple in-memory rate limiter for Responder."""

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

    """

    def __init__(self, requests=100, period=60):
        self.max_requests = requests
        self.period = period
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def _client_key(self, req):
        client = req.client
        if client:
            return client[0]
        return req.headers.get("X-Forwarded-For", "unknown")

    def _cleanup(self, key):
        now = time.time()
        cutoff = now - self.period
        self._buckets[key] = [
            t for t in self._buckets[key] if t > cutoff
        ]

    def check(self, req, resp):
        """Check rate limit. Sets 429 status if exceeded."""
        key = self._client_key(req)
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

    def install(self, api):
        """Install as a before_request hook on the API."""

        @api.route(before_request=True)
        def _rate_limit(req, resp):
            self.check(req, resp)
