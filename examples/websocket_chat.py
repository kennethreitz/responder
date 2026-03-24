# WebSocket chat room example.
# https://responder.kennethreitz.org/tutorial-websockets.html
from starlette.websockets import WebSocketDisconnect

import responder

api = responder.API()

connected = set()


@api.route("/")
def index(req, resp):
    resp.html = """
    <!DOCTYPE html>
    <html>
    <body>
      <h1>Chat Room</h1>
      <div id="messages" style="height:300px;overflow-y:scroll;border:1px solid #ccc;padding:10px;"></div>
      <input id="input" placeholder="Type a message..." style="width:300px;" />
      <script>
        const ws = new WebSocket(`ws://${location.host}/chat`);
        const messages = document.getElementById("messages");
        const input = document.getElementById("input");
        ws.onmessage = (e) => {
          const p = document.createElement("p");
          p.textContent = e.data;
          messages.appendChild(p);
          messages.scrollTop = messages.scrollHeight;
        };
        input.addEventListener("keypress", (e) => {
          if (e.key === "Enter" && input.value) {
            ws.send(input.value);
            input.value = "";
          }
        });
      </script>
    </body>
    </html>
    """  # noqa: E501


@api.route("/chat", websocket=True)
async def chat(ws):
    await ws.accept()
    connected.add(ws)
    try:
        while True:
            message = await ws.receive_text()
            for client in connected:
                await client.send_text(message)
    except WebSocketDisconnect:
        pass
    finally:
        connected.discard(ws)


if __name__ == "__main__":
    api.run()
