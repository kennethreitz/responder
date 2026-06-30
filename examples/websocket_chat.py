"""A tiny WebSocket chat room.

Run it:

    responder run examples/websocket_chat.py

Then open:

    http://127.0.0.1:5042/
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from starlette.websockets import WebSocketDisconnect

import responder


@dataclass
class ChatHub:
    clients: set[Any] = field(default_factory=set)

    async def connect(self, ws) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws) -> None:
        self.clients.discard(ws)

    async def broadcast(self, message: str) -> None:
        for client in tuple(self.clients):
            await client.send_text(message)


def create_api(*, hub: ChatHub | None = None) -> responder.API:
    hub = hub or ChatHub()
    api = responder.API(title="WebSocket Chat", version="1.0", sessions=False)

    @api.get("/")
    def index(req, resp):
        resp.html = """
        <!DOCTYPE html>
        <html>
        <body>
          <h1>Chat Room</h1>
          <div id="messages"
               style="height:300px;overflow-y:scroll;border:1px solid #ccc;padding:10px;"></div>
          <input id="name" placeholder="Name" value="guest" />
          <input id="input" placeholder="Type a message..." style="width:300px;" />
          <script>
            const scheme = location.protocol === "https:" ? "wss" : "ws";
            const name = document.getElementById("name");
            const input = document.getElementById("input");
            const messages = document.getElementById("messages");
            const ws = new WebSocket(`${scheme}://${location.host}/chat?name=${encodeURIComponent(name.value)}`);

            ws.onmessage = (event) => {
              const p = document.createElement("p");
              p.textContent = event.data;
              messages.appendChild(p);
              messages.scrollTop = messages.scrollHeight;
            };

            input.addEventListener("keypress", (event) => {
              if (event.key === "Enter" && input.value) {
                ws.send(input.value);
                input.value = "";
              }
            });
          </script>
        </body>
        </html>
        """  # noqa: E501

    @api.websocket_route("/chat")
    async def chat(ws):
        name = ws.query_params.get("name", "guest") or "guest"
        await hub.connect(ws)
        await hub.broadcast(f"{name} joined")
        try:
            while True:
                message = await ws.receive_text()
                await hub.broadcast(f"{name}: {message}")
        except WebSocketDisconnect:
            pass
        finally:
            hub.disconnect(ws)

    return api


api = create_api()


if __name__ == "__main__":
    api.run()
