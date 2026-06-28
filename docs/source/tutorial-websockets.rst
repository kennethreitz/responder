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
-----------------------------------

You can run code before a WebSocket connection is established, just like
HTTP before-request hooks. This is useful for authentication::

    @api.before_request(websocket=True)
    async def ws_auth(ws):
        # Check for a token in the query string
        # (WebSocket headers are limited in browsers)
        await ws.accept()

WebSocket before-request hooks receive the ``ws`` object and must call
``await ws.accept()`` if they want the connection to proceed.

To reject a connection, close it from the hook — the route handler is
then skipped entirely::

    @api.before_request(websocket=True)
    async def ws_auth(ws):
        if ws.query_params.get("token") != "secret":
            await ws.close(code=4401)  # handler never runs
            return
        await ws.accept()

WebSocket handlers can also receive path parameters and registered
dependencies by declaring them after ``ws``::

    @api.route("/chat/{room}", websocket=True)
    async def chat(ws, *, room):
        await ws.accept()
        await ws.send_text(f"welcome to {room}")

Dependencies behave just as they do for HTTP routes: providers resolve by
parameter name, can depend on one another, and are torn down when the
connection closes. A provider reaches the live socket by naming a
parameter ``ws`` (or ``websocket``) or annotating it ``WebSocket``; the
names ``req``, ``request``, ``resp``, ``response``, ``ws``, and
``websocket`` are reserved and can't be used as dependency names. See the
Dependency Injection section of the :doc:`feature tour <tour>` for the
full picture.


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

When a connection holds a resource — a database session, a lock, a slot
in a presence registry — reach for a generator dependency instead of a
hand-rolled ``try/finally``. Responder runs the teardown after ``yield``
when the handler returns *or* when the client disconnects, so cleanup is
guaranteed even if the socket drops mid-stream::

    @api.dependency()
    async def db():
        conn = await pool.acquire()
        yield conn
        await pool.release(conn)  # runs when the connection closes

    @api.route("/feed", websocket=True)
    async def feed(ws, *, db):
        await ws.accept()
        while True:
            row = await db.fetch_next()
            await ws.send_json(row)


Testing WebSockets
------------------

Responder's test client — ``api.requests``, a Starlette ``TestClient`` —
speaks WebSocket too. ``websocket_connect`` is a context manager that
opens the connection on enter and closes it on exit::

    def test_echo():
        with api.requests.websocket_connect("/ws") as ws:
            ws.send_text("hello")
            assert ws.receive_text() == "Echo: hello"

You can also assert that a connection is rejected::

    import pytest
    from starlette.websockets import WebSocketDisconnect

    def test_rejects_unknown_route():
        with pytest.raises(WebSocketDisconnect):
            with api.requests.websocket_connect("/nonexistent"):
                pass

See :doc:`the testing guide <testing>` for fixtures and the rest of the
in-process testing story.
