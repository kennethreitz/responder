"""WebSocket idle timeout: close a connection that goes quiet too long.

The deadline resets on every inbound message, so it bounds idle time between
messages rather than the total connection lifetime.
"""

import time

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import responder


def _client(api):
    return TestClient(api, base_url="http://;")


def test_idle_websocket_is_closed_with_1001():
    api = responder.API(
        allowed_hosts=[";"], session_https_only=False, ws_idle_timeout=0.2
    )

    @api.route("/ws", websocket=True)
    async def ws_handler(ws):
        await ws.accept()
        # Block waiting for a client message that never arrives.
        await ws.receive_text()

    with _client(api).websocket_connect("ws://;/ws") as ws:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_text()
    assert excinfo.value.code == 1001


def test_active_websocket_survives_beyond_the_idle_window():
    # The deadline is per-message: a client that keeps talking, with no single
    # gap exceeding the timeout, stays connected even though the total lifetime
    # (0.6s of gaps) exceeds the 0.5s idle timeout.
    api = responder.API(
        allowed_hosts=[";"], session_https_only=False, ws_idle_timeout=0.5
    )

    @api.route("/ws", websocket=True)
    async def echo(ws):
        await ws.accept()
        try:
            while True:
                msg = await ws.receive_text()
                await ws.send_text(msg)
        except WebSocketDisconnect:
            pass

    with _client(api).websocket_connect("ws://;/ws") as ws:
        for i in range(3):
            time.sleep(0.2)  # < timeout, so the connection must stay open
            ws.send_text(f"ping-{i}")
            assert ws.receive_text() == f"ping-{i}"


def test_no_timeout_by_default():
    # Without ws_idle_timeout, receive is not wrapped and behaves normally.
    api = responder.API(allowed_hosts=[";"], session_https_only=False)

    @api.route("/ws", websocket=True)
    async def echo(ws):
        await ws.accept()
        msg = await ws.receive_text()
        await ws.send_text(msg)

    with _client(api).websocket_connect("ws://;/ws") as ws:
        ws.send_text("hi")
        assert ws.receive_text() == "hi"
