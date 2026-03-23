"""Mount marimo notebooks inside a Responder API.

Requirements:
    pip install responder marimo

Usage:
    python examples/marimo_mount.py

Then visit:
    http://127.0.0.1:5042/hello       → Responder JSON endpoint
    http://127.0.0.1:5042/notebooks/  → Interactive marimo notebook
"""

import marimo

import responder

api = responder.API()


@api.route("/hello")
def hello(req, resp):
    resp.media = {"message": "Hello from Responder!"}


# Mount marimo notebooks at /notebooks
server = marimo.create_asgi_app().with_app(path="", root="notebooks/hello.py")
api.mount("/notebooks", server.build())

if __name__ == "__main__":
    api.run()
