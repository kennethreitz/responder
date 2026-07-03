"""Built-in request metrics with Prometheus text exposition.

Enabled via ``API(metrics_route="/metrics")`` — no external dependencies.
"""

from __future__ import annotations

import threading
from collections import defaultdict

# Default histogram bucket upper bounds, in seconds.
BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def _escape_label_value(value: str) -> str:
    """Escape a label value per the Prometheus text exposition format.

    Backslash, double-quote, and newline must be escaped inside quoted
    label values; anything else passes through verbatim.
    """
    return (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    )


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
        self.collector.track_in_flight(1)
        try:
            await self.app(scope, receive, recording_send)
        finally:
            self.collector.track_in_flight(-1)
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

    def __init__(self, buckets: tuple[float, ...] = BUCKETS):
        """Create a collector.

        :param buckets: Histogram bucket upper bounds, in seconds, in
            strictly ascending order (defaults to :data:`BUCKETS`). Tune
            these to your latency profile — e.g. add ``30.0`` for slow
            report endpoints, or ``0.001`` for sub-millisecond cache hits.
            An implicit ``+Inf`` bucket is always appended on render.
        """
        buckets = tuple(buckets)
        if not buckets:
            raise ValueError("buckets must not be empty")
        if any(b >= n for b, n in zip(buckets, buckets[1:], strict=False)):
            raise ValueError(f"buckets must be strictly ascending: {buckets!r}")
        self.buckets = buckets
        self.requests: dict[tuple[str, str, str], int] = defaultdict(int)
        self.latency_sum: dict[tuple[str, str], float] = defaultdict(float)
        self.latency_count: dict[tuple[str, str], int] = defaultdict(int)
        self.latency_buckets: dict[tuple[str, str, float], int] = defaultdict(int)
        self.in_flight = 0
        # Guards the dicts: record() runs per request (possibly from a thread
        # pool / concurrent tasks) while render() iterates them on a /metrics
        # scrape. Without this an in-flight record() can raise "dictionary
        # changed size during iteration" in render(), and increments are lost
        # on free-threaded CPython.
        self._lock = threading.Lock()

    def track_in_flight(self, delta: int) -> None:
        """Adjust the in-flight request gauge by ``delta`` (+1 / -1)."""
        with self._lock:
            self.in_flight += delta

    def record(self, method: str, path: str, status: int, duration: float) -> None:
        key = (method, path)
        with self._lock:
            self.requests[(method, path, str(status))] += 1
            self.latency_sum[key] += duration
            self.latency_count[key] += 1
            for bound in self.buckets:
                if duration <= bound:
                    self.latency_buckets[(method, path, bound)] += 1

    def render(self) -> str:
        """The collected metrics in Prometheus text exposition format."""
        # Snapshot under the lock, then format without holding it.
        with self._lock:
            requests = dict(self.requests)
            latency_sum = dict(self.latency_sum)
            latency_count = dict(self.latency_count)
            latency_buckets = dict(self.latency_buckets)
            in_flight = self.in_flight
        esc = _escape_label_value
        lines = [
            "# HELP responder_requests_total Total HTTP requests.",
            "# TYPE responder_requests_total counter",
        ]
        for (method, path, status), count in sorted(requests.items()):
            lines.append(
                f'responder_requests_total{{method="{esc(method)}",'
                f'path="{esc(path)}",status="{esc(status)}"}} {count}'
            )

        lines += [
            "# HELP responder_requests_in_flight "
            "HTTP requests currently being handled.",
            "# TYPE responder_requests_in_flight gauge",
            f"responder_requests_in_flight {in_flight}",
        ]

        lines += [
            "# HELP responder_request_duration_seconds HTTP request latency.",
            "# TYPE responder_request_duration_seconds histogram",
        ]
        for (method, path), count in sorted(latency_count.items()):
            for bound in self.buckets:
                cumulative = latency_buckets.get((method, path, bound), 0)
                lines.append(
                    f"responder_request_duration_seconds_bucket"
                    f'{{method="{esc(method)}",'
                    f'path="{esc(path)}",le="{bound}"}} {cumulative}'
                )
            lines.append(
                f"responder_request_duration_seconds_bucket"
                f'{{method="{esc(method)}",'
                f'path="{esc(path)}",le="+Inf"}} {count}'
            )
            lines.append(
                f'responder_request_duration_seconds_sum{{method="{esc(method)}",'
                f'path="{esc(path)}"}} {latency_sum[(method, path)]:.6f}'
            )
            lines.append(
                f'responder_request_duration_seconds_count{{method="{esc(method)}",'
                f'path="{esc(path)}"}} {count}'
            )
        return "\n".join(lines) + "\n"
