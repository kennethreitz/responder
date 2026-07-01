"""MemorySessionBackend keeps memory bounded:

- at most ``max_keys`` sessions are stored (earliest-expiring entries are
  evicted beyond the cap; the OrderedDict is kept ordered by expiry)
- expired entries are swept opportunistically, not only on ``get()``
- get/set/delete are lock-guarded (they run on threadpool workers)
"""

import threading
import time

from responder.ext.sessions import MemorySessionBackend

# --------------------------------------------------------------------------
# Bounded keys — eviction in expiry order
# --------------------------------------------------------------------------


def test_store_evicts_earliest_expiring_sessions():
    backend = MemorySessionBackend(max_keys=3)
    for i in range(10):
        backend.set(f"sid-{i}", {"n": i}, max_age=60)
    assert len(backend._store) <= 3
    # The most recent sessions are retained; the oldest are gone.
    assert backend.get("sid-9") == {"n": 9}
    assert backend.get("sid-0") is None


def test_set_refreshes_eviction_position():
    backend = MemorySessionBackend(max_keys=2)
    backend.set("a", {"v": 1}, max_age=60)
    backend.set("b", {"v": 2}, max_age=60)
    backend.set("a", {"v": 1}, max_age=60)  # rewrite: new expiry, back of queue
    backend.set("c", {"v": 3}, max_age=60)  # evicts "b", not "a"
    assert backend.get("a") == {"v": 1}
    assert backend.get("b") is None
    assert backend.get("c") == {"v": 3}


def test_touch_refreshes_eviction_position():
    backend = MemorySessionBackend(max_keys=2)
    backend.set("a", {"v": 1}, max_age=60)
    backend.set("b", {"v": 2}, max_age=60)
    backend.touch("a", max_age=60)  # slides expiry, so "a" moves to the back
    backend.set("c", {"v": 3}, max_age=60)  # evicts "b", not "a"
    assert backend.get("a") == {"v": 1}
    assert backend.get("b") is None
    assert backend.get("c") == {"v": 3}


def test_cap_eviction_prefers_expiring_soon_over_live(monkeypatch):
    """Regression: a plain get() must not push a nearly-expired entry to the
    back of the eviction queue — under cap pressure that evicted a LIVE
    session while the expired-soon entry kept its slot."""
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])

    backend = MemorySessionBackend(max_keys=2)
    backend.set("soon", {"v": 1}, max_age=10)  # expires at t=1010
    backend.set("live", {"v": 2}, max_age=100)  # expires at t=1100

    clock[0] = 1005.0
    # Reading "soon" (still valid) must not reorder it behind "live".
    assert backend.get("soon") == {"v": 1}

    # Cap pressure: the entry expiring first ("soon") is the victim.
    backend.set("new", {"v": 3}, max_age=100)
    assert backend.get("live") == {"v": 2}
    assert backend.get("new") == {"v": 3}
    assert backend.get("soon") is None


def test_default_cap_is_100_000():
    assert MemorySessionBackend()._max_keys == 100_000


# --------------------------------------------------------------------------
# Expired-entry sweep
# --------------------------------------------------------------------------


def test_expired_sessions_are_swept_on_write():
    backend = MemorySessionBackend(max_keys=100)
    for i in range(5):
        backend.set(f"stale-{i}", {"n": i}, max_age=60)
    # Expire them all without ever get()-ing them (abandoned sessions).
    now = time.time()
    backend._store = type(backend._store)(
        (sid, (data, now - 1)) for sid, (data, _) in backend._store.items()
    )
    backend.set("fresh", {"ok": True}, max_age=60)
    assert "fresh" in backend._store
    for i in range(5):
        assert f"stale-{i}" not in backend._store


def test_expired_session_still_misses_on_get():
    backend = MemorySessionBackend()
    backend.set("sid", {"user": "kenneth"}, max_age=60)
    data, _ = backend._store["sid"]
    backend._store["sid"] = (data, time.time() - 1)
    assert backend.get("sid") is None
    assert "sid" not in backend._store


# --------------------------------------------------------------------------
# Pre-existing happy path
# --------------------------------------------------------------------------


def test_set_get_touch_delete_roundtrip():
    backend = MemorySessionBackend()
    backend.set("sid", {"user": "kenneth"}, max_age=60)
    assert backend.get("sid") == {"user": "kenneth"}
    backend.touch("sid", max_age=120)
    assert backend.get("sid") == {"user": "kenneth"}
    backend.delete("sid")
    assert backend.get("sid") is None
    backend.delete("sid")  # deleting a missing session is a no-op


def test_touch_missing_session_is_noop():
    backend = MemorySessionBackend()
    backend.touch("nope", max_age=60)
    assert backend.get("nope") is None


# --------------------------------------------------------------------------
# Thread safety
# --------------------------------------------------------------------------


def test_concurrent_set_get_delete_is_safe_and_bounded():
    backend = MemorySessionBackend(max_keys=50)
    errors = []

    def worker(offset):
        try:
            for i in range(500):
                sid = f"sid-{offset}-{i % 75}"
                backend.set(sid, {"n": i}, max_age=60)
                backend.get(sid)
                if i % 7 == 0:
                    backend.delete(sid)
        except Exception as exc:  # an unlocked version can corrupt the dict
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(backend._store) <= 50
