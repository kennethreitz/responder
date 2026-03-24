# Example showing the lifespan context manager pattern.
# https://pypi.org/project/responder/
from contextlib import asynccontextmanager

import responder


@asynccontextmanager
async def lifespan(app):
    # Startup: initialize resources
    print("Starting up...")
    yield
    # Shutdown: clean up resources
    print("Shutting down...")


api = responder.API(lifespan=lifespan)


@api.route("/{greeting}")
async def greet_world(req, resp, *, greeting):
    resp.text = f"{greeting}, world!"


if __name__ == "__main__":
    api.run()
