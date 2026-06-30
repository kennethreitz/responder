"""Use a lifespan context manager for startup and shutdown work.

Run it:

    responder run examples/lifespan.py

Try it with:

    curl http://127.0.0.1:5042/health
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import responder


def create_api() -> responder.API:
    state = {"ready": False}

    @asynccontextmanager
    async def lifespan(app):
        print("Starting up...")
        state["ready"] = True
        try:
            yield
        finally:
            state["ready"] = False
            print("Shutting down...")

    api = responder.API(lifespan=lifespan, sessions=False)

    @api.get("/health")
    def health(req, resp):
        resp.media = {"ready": state["ready"]}

    @api.get("/{greeting}")
    def greet_world(req, resp, *, greeting: str):
        resp.text = f"{greeting}, world!"

    return api


api = create_api()


if __name__ == "__main__":
    api.run()
