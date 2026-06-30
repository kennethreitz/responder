"""The smallest useful Responder app.

Run it:

    responder run examples/helloworld.py

Try it with:

    curl http://127.0.0.1:5042/hello
"""

from __future__ import annotations

import responder

api = responder.API(sessions=False)


@api.get("/")
def index(req, resp):
    resp.text = "hello, world!"


@api.get("/{greeting}")
def greet_world(req, resp, *, greeting: str):
    resp.text = f"{greeting}, world!"


if __name__ == "__main__":
    api.run()
