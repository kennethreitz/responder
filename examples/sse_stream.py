"""Server-Sent Events streaming example.

Run it:

    responder run examples/sse_stream.py

Try it with:

    curl -N http://127.0.0.1:5042/stream
"""

from __future__ import annotations

import asyncio

import responder


def create_api(*, event_count: int = 20, delay: float = 0.5) -> responder.API:
    api = responder.API(
        title="SSE Stream",
        version="1.0",
        openapi="3.1.0",
        docs_route="/docs",
        sessions=False,
    )

    @api.get("/", include_in_schema=False)
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
            source.addEventListener("tick", (event) => {
              const p = document.createElement("p");
              p.textContent = event.data;
              events.appendChild(p);
            });
          </script>
        </body>
        </html>
        """

    @api.get(
        "/stream",
        operation_id="stream_events",
        tags=["events"],
        summary="Stream events",
        responses={200: "A text/event-stream response."},
    )
    async def stream(req, resp):
        @resp.sse(heartbeat=15)
        async def events():
            for event_id in range(1, event_count + 1):
                yield {
                    "id": str(event_id),
                    "event": "tick",
                    "data": f"Event #{event_id}",
                }
                if delay:
                    await asyncio.sleep(delay)

    return api


api = create_api()


if __name__ == "__main__":
    api.run()
