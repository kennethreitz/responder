"""Built-in request metrics with Prometheus text exposition.

Enabled via ``API(metrics_route="/metrics")`` — no external dependencies.
"""

from __future__ import annotations

from collections import defaultdict

# Histogram bucket upper bounds, in seconds.
BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class MetricsMiddleware:
    """ASGI middleware that records request counts and latency.

    Sits just outside the exception middleware so error responses
    (404s, 500s) are observed with their real status codes.
    """

    def __init__(self, app, collector):
        self.app = app
        self.collector = collector

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        import time

        status_holder = {"status": 0}

        async def recording_send(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        start = time.perf_counter()
        try:
            await self.app(scope, receive, recording_send)
        finally:
            # The router stamps scope["route_pattern"] during resolution.
            self.collector.record(
                scope.get("method", ""),
                scope.get("route_pattern", "unmatched"),
                status_holder["status"] or 500,
                time.perf_counter() - start,
            )


class MetricsCollector:
    """Collects per-route request counts and latency histograms.

    Labels use the route *pattern* (``/users/{id}``), not the raw path,
    so cardinality stays bounded. Requests that match no route are
    labelled ``unmatched``.
    """

    def __init__(self):
        self.requests: dict[tuple[str, str, str], int] = defaultdict(int)
        self.latency_sum: dict[tuple[str, str], float] = defaultdict(float)
        self.latency_count: dict[tuple[str, str], int] = defaultdict(int)
        self.latency_buckets: dict[tuple[str, str, float], int] = defaultdict(int)

    def record(self, method: str, path: str, status: int, duration: float) -> None:
        self.requests[(method, path, str(status))] += 1
        key = (method, path)
        self.latency_sum[key] += duration
        self.latency_count[key] += 1
        for bound in BUCKETS:
            if duration <= bound:
                self.latency_buckets[(method, path, bound)] += 1

    def render(self) -> str:
        """The collected metrics in Prometheus text exposition format."""
        lines = [
            "# HELP responder_requests_total Total HTTP requests.",
            "# TYPE responder_requests_total counter",
        ]
        for (method, path, status), count in sorted(self.requests.items()):
            lines.append(
                f'responder_requests_total{{method="{method}",path="{path}",'
                f'status="{status}"}} {count}'
            )

        lines += [
            "# HELP responder_request_duration_seconds HTTP request latency.",
            "# TYPE responder_request_duration_seconds histogram",
        ]
        for (method, path), count in sorted(self.latency_count.items()):
            cumulative = 0
            for bound in BUCKETS:
                cumulative = self.latency_buckets.get((method, path, bound), 0)
                lines.append(
                    f'responder_request_duration_seconds_bucket{{method="{method}",'
                    f'path="{path}",le="{bound}"}} {cumulative}'
                )
            lines.append(
                f'responder_request_duration_seconds_bucket{{method="{method}",'
                f'path="{path}",le="+Inf"}} {count}'
            )
            lines.append(
                f'responder_request_duration_seconds_sum{{method="{method}",'
                f'path="{path}"}} {self.latency_sum[(method, path)]:.6f}'
            )
            lines.append(
                f'responder_request_duration_seconds_count{{method="{method}",'
                f'path="{path}"}} {count}'
            )
        return "\n".join(lines) + "\n"
