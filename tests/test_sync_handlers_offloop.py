"""Sync lifecycle and exception handlers must run off the event loop.

A blocking sync handler executed directly on the loop stalls the whole
server. Both paths now route through ``run_in_threadpool``. We detect this
by calling ``asyncio.get_running_loop()`` inside the handler: it succeeds on
the loop thread but raises ``RuntimeError`` inside a threadpool worker.
"""

import asyncio

import responder


def _ran_off_loop():
    try:
        asyncio.get_running_loop()
        return False
    except RuntimeError:
        return True


def test_sync_startup_handler_runs_off_event_loop():
    api = responder.API(allowed_hosts=[";"], session_https_only=False)
    seen = {}

    @api.on_event("startup")
    def startup():
        seen["off_loop"] = _ran_off_loop()

    with api.requests:
        pass

    assert seen["off_loop"] is True


def test_sync_shutdown_handler_runs_off_event_loop():
    api = responder.API(allowed_hosts=[";"], session_https_only=False)
    seen = {}

    @api.on_event("shutdown")
    def shutdown():
        seen["off_loop"] = _ran_off_loop()

    with api.requests:
        pass

    assert seen["off_loop"] is True


def test_sync_exception_handler_runs_off_event_loop():
    api = responder.API(allowed_hosts=[";"], session_https_only=False)
    seen = {}

    @api.exception_handler(ValueError)
    def handle(req, resp, exc):
        seen["off_loop"] = _ran_off_loop()
        resp.media = {"error": "handled"}

    @api.route("/boom")
    def boom(req, resp):
        raise ValueError("nope")

    r = api.requests.get("/boom")
    assert seen["off_loop"] is True
    assert r.json() == {"error": "handled"}
