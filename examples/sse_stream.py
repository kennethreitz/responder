# Server-Sent Events streaming example.
# https://responder.kennethreitz.org/tour.html#server-sent-events-sse
import asyncio

import responder

api = responder.API()


@api.route("/")
def index(req, resp):
    resp.html = """
    <!DOCTYPE html>
    <html>
    <body>
      <h1>SSE Stream</h1>
      <div id="events"></div>
      <script>
        const source = new EventSource("/stream");
        const events = document.getElementById("events");
        source.onmessage = (e) => {
          const p = document.createElement("p");
          p.textContent = e.data;
          events.appendChild(p);
        };
      </script>
    </body>
    </html>
    """


@api.route("/stream")
async def stream(req, resp):
    @resp.sse
    async def events():
        for i in range(20):
            yield {"data": f"Event #{i}"}
            await asyncio.sleep(0.5)


if __name__ == "__main__":
    api.run()
