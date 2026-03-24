WebSocket Tutorial
==================

HTTP is request-response — the client asks, the server answers, and the
connection closes. WebSockets upgrade that into a persistent, bidirectional
channel where both sides can send messages at any time. This is what powers
chat apps, live dashboards, multiplayer games, and collaborative editors.

This tutorial builds a simple chat room to show how WebSockets work in
Responder.


How WebSockets Work
-------------------

1. The client sends a normal HTTP request with an ``Upgrade: websocket``
   header.
2. The server accepts the upgrade and the connection switches protocols.
3. Both sides can now send messages freely — no more request/response.
4. Either side can close the connection at any time.

In Responder, WebSocket routes receive a ``ws`` object instead of
``req`` and ``resp``. The ``ws`` object has methods for accepting the
connection, sending and receiving data, and closing.


Echo Server
-----------

The simplest WebSocket — echoes everything back::

    @api.route("/ws", websocket=True)
    async def echo(ws):
        await ws.accept()
        while True:
            data = await ws.receive_text()
            await ws.send_text(f"Echo: {data}")

The ``await ws.accept()`` call completes the WebSocket handshake. After
that, you're in a loop — receive a message, send a response.

Test it with a WebSocket client::

    $ pip install websocket-client
    $ python -c "
    import websocket
    ws = websocket.create_connection('ws://localhost:5042/ws')
    ws.send('hello')
    print(ws.recv())  # Echo: hello
    ws.close()
    "


Chat Room
---------

A chat room needs to broadcast messages to all connected clients. We keep
a set of active connections and iterate through them when someone sends
a message::

    from starlette.websockets import WebSocketDisconnect

    connected = set()

    @api.route("/chat", websocket=True)
    async def chat(ws):
        await ws.accept()
        connected.add(ws)
        try:
            while True:
                message = await ws.receive_text()
                # Broadcast to all connected clients
                for client in connected:
                    await client.send_text(message)
        except WebSocketDisconnect:
            pass
        finally:
            connected.discard(ws)

The ``try/finally`` block ensures we remove disconnected clients from
the set, even if the connection drops unexpectedly. Catching
``WebSocketDisconnect`` specifically (rather than bare ``Exception``)
makes the intent clear and avoids swallowing real bugs.


Data Formats
------------

WebSockets support three data formats:

**Text** — plain strings::

    await ws.send_text("hello")
    message = await ws.receive_text()

**JSON** — auto-serialized Python objects::

    await ws.send_json({"type": "update", "data": [1, 2, 3]})
    message = await ws.receive_json()

**Binary** — raw bytes, useful for images, audio, or custom protocols::

    await ws.send_bytes(b"\x00\x01\x02")
    data = await ws.receive_bytes()


HTML Client
-----------

Here's a minimal HTML page that connects to the chat room. The browser's
built-in ``WebSocket`` API handles everything — no libraries needed:

.. code-block:: html

    <!DOCTYPE html>
    <html>
    <body>
      <div id="messages"></div>
      <input id="input" placeholder="Type a message..." />
      <script>
        const ws = new WebSocket("ws://localhost:5042/chat");
        const messages = document.getElementById("messages");
        const input = document.getElementById("input");

        ws.onmessage = (event) => {
          const p = document.createElement("p");
          p.textContent = event.data;
          messages.appendChild(p);
        };

        input.addEventListener("keypress", (e) => {
          if (e.key === "Enter") {
            ws.send(input.value);
            input.value = "";
          }
        });
      </script>
    </body>
    </html>

Save this as ``static/index.html`` and serve it with Responder's
built-in static file support.


Before-Request Hooks for WebSockets
------------------------------------

You can run code before a WebSocket connection is established, just like
HTTP before-request hooks. This is useful for authentication::

    @api.before_request(websocket=True)
    async def ws_auth(ws):
        # Check for a token in the query string
        # (WebSocket headers are limited in browsers)
        await ws.accept()

WebSocket before-request hooks receive the ``ws`` object and must call
``await ws.accept()`` if they want the connection to proceed.


Connection Lifecycle
--------------------

WebSocket connections go through several states:

1. **Connecting** — the client sends an upgrade request
2. **Open** — after ``await ws.accept()``, both sides can send messages
3. **Closing** — either side initiates a close handshake
4. **Closed** — the connection is fully terminated

When a client disconnects (closes the tab, loses network), the next
``await ws.receive_text()`` raises ``WebSocketDisconnect``. Always
handle this — otherwise your server accumulates dead connections::

    from starlette.websockets import WebSocketDisconnect

    @api.route("/ws", websocket=True)
    async def handler(ws):
        await ws.accept()
        try:
            while True:
                data = await ws.receive_text()
                await ws.send_text(f"Got: {data}")
        except WebSocketDisconnect:
            print("Client disconnected")

You can also close connections from the server side::

    await ws.close(code=1000)  # 1000 = normal closure

Common close codes: ``1000`` (normal), ``1001`` (going away),
``1008`` (policy violation), ``1011`` (server error).


Testing WebSockets
------------------

Use Starlette's ``TestClient`` for WebSocket tests::

    from starlette.testclient import TestClient

    def test_echo():
        client = TestClient(api)
        with client.websocket_connect("/ws") as ws:
            ws.send_text("hello")
            assert ws.receive_text() == "Echo: hello"

The ``websocket_connect`` context manager handles the connection
lifecycle — it connects on enter and disconnects on exit.

You can also test that connections are properly rejected::

    from starlette.websockets import WebSocketDisconnect

    def test_websocket_404():
        client = TestClient(api)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/nonexistent"):
                pass
