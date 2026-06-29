"""v6.1: production-grade Server-Sent Events (resp.sse)."""

import asyncio

from starlette.testclient import TestClient

import responder


def _api():
    return responder.API(allowed_hosts=[";"], secret_key="x" * 8, session_https_only=False)


def _client(api):
    return TestClient(api, base_url="http://;")


def test_sse_headers():
    api = _api()

    @api.route("/s")
    async def s(req, resp):
        @resp.sse
        async def stream():
            yield "hi"

    r = _client(api).get("/s")
    assert "text/event-stream" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-accel-buffering"] == "no"


def test_sse_dict_data_is_json_encoded():
    api = _api()

    @api.route("/s")
    async def s(req, resp):
        @resp.sse
        async def stream():
            yield {"data": {"n": 1, "ok": True}, "id": "e1", "event": "tick"}

    body = _client(api).get("/s").text
    assert 'data: {"n": 1, "ok": true}' in body
    assert "id: e1" in body
    assert "event: tick" in body


def test_sse_comment_and_plain_string():
    api = _api()

    @api.route("/s")
    async def s(req, resp):
        @resp.sse
        async def stream():
            yield "plain"
            yield {"comment": "keepalive"}

    body = _client(api).get("/s").text
    assert "data: plain" in body
    assert ": keepalive" in body


def test_sse_multiline_data():
    api = _api()

    @api.route("/s")
    async def s(req, resp):
        @resp.sse
        async def stream():
            yield {"data": "line1\nline2"}

    body = _client(api).get("/s").text
    assert "data: line1\ndata: line2" in body


def test_sse_heartbeat_injects_keepalives():
    api = _api()

    @api.route("/s")
    async def s(req, resp):
        @resp.sse(heartbeat=0.02)
        async def stream():
            await asyncio.sleep(0.07)  # idle longer than the heartbeat
            yield {"data": "done"}

    body = _client(api).get("/s").text
    assert ": keepalive" in body
    assert "data: done" in body


def test_request_last_event_id():
    api = _api()

    @api.route("/e")
    async def e(req, resp):
        resp.media = {"id": req.last_event_id}

    client = _client(api)
    assert client.get("/e", headers={"Last-Event-ID": "evt-42"}).json() == {"id": "evt-42"}
    assert client.get("/e").json() == {"id": None}
