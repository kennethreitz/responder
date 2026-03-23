# Responder

A familiar HTTP Service Framework for Python, powered by [Starlette](https://www.starlette.io/).

```python
import responder

api = responder.API()

@api.route("/{greeting}")
async def greet_world(req, resp, *, greeting):
    resp.text = f"{greeting}, world!"

if __name__ == "__main__":
    api.run()
```

    $ pip install responder

That's it. Supports Python 3.10+.

## The Basics

- `resp.text` sends back text. `resp.html` sends back HTML. `resp.content` sends back bytes.
- `resp.media` sends back JSON (or YAML, with content negotiation).
- `resp.file("path.pdf")` serves a file with automatic content-type detection.
- `req.headers` is case-insensitive. `req.params` gives you query parameters.
- Both sync and async views work — the `async` is optional.

## Highlights

```python
# Type-safe route parameters
@api.route("/users/{user_id:int}")
async def get_user(req, resp, *, user_id):
    resp.media = {"id": user_id}

# HTTP method filtering
@api.route("/items", methods=["POST"])
async def create_item(req, resp):
    data = await req.media()
    resp.media = {"created": data}

# Class-based views
@api.route("/things/{id}")
class ThingResource:
    def on_get(self, req, resp, *, id):
        resp.media = {"id": id}
    def on_post(self, req, resp, *, id):
        resp.text = "created"

# Before-request hooks (auth, rate limiting, etc.)
@api.route(before_request=True)
def check_auth(req, resp):
    if not req.headers.get("Authorization"):
        resp.status_code = 401
        resp.media = {"error": "unauthorized"}

# Custom error handling
@api.exception_handler(ValueError)
async def handle_error(req, resp, exc):
    resp.status_code = 400
    resp.media = {"error": str(exc)}

# Lifespan events
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    print("starting up")
    yield
    print("shutting down")

api = responder.API(lifespan=lifespan)

# GraphQL
import graphene
api.graphql("/graphql", schema=graphene.Schema(query=Query))

# WebSockets
@api.route("/ws", websocket=True)
async def websocket(ws):
    await ws.accept()
    while True:
        name = await ws.receive_text()
        await ws.send_text(f"Hello {name}!")

# Mount WSGI/ASGI apps
from flask import Flask
flask_app = Flask(__name__)
api.mount("/flask", flask_app)

# Background tasks
@api.route("/work")
def do_work(req, resp):
    @api.background.task
    def process():
        import time; time.sleep(10)
    process()
    resp.media = {"status": "processing"}
```

Built-in OpenAPI docs, cookie-based sessions, gzip compression, static file serving, Jinja2 templates, and a production uvicorn server.

Route convertors: `str`, `int`, `float`, `uuid`, `path`.

## Documentation

https://responder.kennethreitz.org
