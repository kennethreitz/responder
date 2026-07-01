"""Concurrency & resource-exhaustion hardening:

- MetricsCollector is lock-guarded (no crash / lost counts under concurrency)
- rate-limit MemoryBackend evicts keys (bounded memory)
- SSE heartbeat uses a bounded queue (backpressure, correct delivery)
- Templates use a dedicated async env (no shared is_async toggle race)
"""

import asyncio
import threading

from responder.ext.metrics import MetricsCollector
from responder.ext.ratelimit import MemoryBackend
from responder.models import _sse_with_heartbeat
from responder.templates import Templates

# --------------------------------------------------------------------------
# Metrics collector — concurrent record() / render()
# --------------------------------------------------------------------------


def test_metrics_collector_concurrent_record_and_render():
    collector = MetricsCollector()
    errors = []
    stop = threading.Event()

    def writer():
        while not stop.is_set():
            collector.record("GET", "/x", 200, 0.01)

    def reader():
        try:
            while not stop.is_set():
                collector.render()
        except Exception as exc:  # a lock-free version raised "changed size"
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(4)]
    threads += [threading.Thread(target=reader) for _ in range(2)]
    for t in threads:
        t.start()
    threading.Event().wait(0.2)
    stop.set()
    for t in threads:
        t.join()

    assert not errors
    # No increments were lost to races.
    assert collector.requests[("GET", "/x", "200")] > 0


def test_metrics_record_counts_are_exact_under_threads():
    collector = MetricsCollector()
    n = 2000
    threads = [
        threading.Thread(
            target=lambda: [collector.record("GET", "/y", 200, 0.01) for _ in range(n)]
        )
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert collector.requests[("GET", "/y", "200")] == n * 4


# --------------------------------------------------------------------------
# Rate-limit MemoryBackend — bounded keys
# --------------------------------------------------------------------------


def test_memory_backend_evicts_least_recently_used_keys():
    backend = MemoryBackend(max_keys=3)
    for i in range(10):
        backend.hit(f"ip-{i}", max_requests=5, period=60)
    assert len(backend._buckets) <= 3
    # The most recent keys are retained.
    assert "ip-9" in backend._buckets
    assert "ip-0" not in backend._buckets


def test_memory_backend_still_limits_within_cap():
    backend = MemoryBackend(max_keys=100)
    results = [backend.hit("client", max_requests=2, period=60) for _ in range(3)]
    assert [allowed for allowed, _ in results] == [True, True, False]


# --------------------------------------------------------------------------
# SSE heartbeat — bounded queue delivers all items in order
# --------------------------------------------------------------------------


def test_sse_heartbeat_bounded_queue_delivers_in_order():
    async def produce():
        for i in range(50):
            yield {"data": str(i)}

    async def drain():
        seen = []
        # Tiny queue + a long heartbeat interval so no keepalives fire.
        async for item in _sse_with_heartbeat(produce(), interval=100, maxsize=2):
            seen.append(item["data"])
        return seen

    seen = asyncio.run(drain())
    assert seen == [str(i) for i in range(50)]


# --------------------------------------------------------------------------
# Templates — dedicated async environment, no shared toggle
# --------------------------------------------------------------------------


def test_templates_use_separate_async_env(tmp_path):
    (tmp_path / "t.html").write_text("{{ var }}")
    templates = Templates(directory=tmp_path)

    assert templates._env.is_async is False
    assert templates._async_env.is_async is True

    async def render_it():
        return await templates.render_async("t.html", var="hi")

    assert asyncio.run(render_it()) == "hi"
    # The sync env's is_async was never toggled by the async render.
    assert templates._env.is_async is False
    # Sync render still works afterward.
    assert templates.render("t.html", var="yo") == "yo"


def test_templates_share_globals_across_envs(tmp_path):
    (tmp_path / "t.html").write_text("{{ g }}")
    templates = Templates(directory=tmp_path, context={"g": "global"})
    assert templates.render("t.html") == "global"

    async def render_it():
        return await templates.render_async("t.html")

    assert asyncio.run(render_it()) == "global"
