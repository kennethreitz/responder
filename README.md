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

That's it. Supports Python 3.11+.

## The Basics

- `resp.text` sends back text. `resp.html` sends back HTML. `resp.content` sends back bytes.
- `resp.media` sends back JSON (or YAML, with content negotiation).
- `resp.file("path.pdf")` serves a file with automatic content-type detection.
- `File(...)` uploads use streamed `UploadFile` objects; `await file.save(path)` writes them to disk.
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

# Route-local hooks
def require_json(req, resp):
    if not req.is_json:
        resp.status_code = 415
        resp.media = {"error": "JSON required"}

@api.post("/events", before=require_json)
async def events(req, resp):
    resp.media = await req.media()

# Local dependencies
from responder import Depends

def current_user(req):
    return req.headers.get("X-User")

@api.get("/me")
def me(req, resp, *, user=Depends(current_user)):
    resp.media = {"user": user}

# Side-effect dependencies
@api.get("/ready", dependencies=[Depends(current_user)])
def ready(req, resp):
    resp.media = {"ready": True}

# Route-level auth with OpenAPI security
from responder.ext.auth import BearerAuth

auth = BearerAuth(tokens=["s3cret"])

@api.get("/private", auth=auth)
def private(req, resp, *, user):
    resp.media = {"user": user}

# App-level auth with public route opt-out
secured_api = responder.API(auth=auth)

@secured_api.get("/health", auth=None)
def health(req, resp):
    resp.media = {"ok": True}

# Optional auth accepts anonymous requests but still rejects bad credentials
optional_auth = auth.optional()

@api.get("/maybe", auth=optional_auth)
def maybe(req, resp, *, user):
    resp.media = {"user": user}

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

# Problem-details enrichment
def problem_handler(payload, request, exc):
    payload["type"] = f"https://example.com/problems/{payload['status']}"
    payload["instance"] = request.url.path

api = responder.API(problem_handler=problem_handler, request_id=True)

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
Install `responder[server]` to add Granian for production ASGI serving.

Route convertors: `str`, `int`, `float`, `uuid`, `path`.

Framework errors use RFC 9457-style `application/problem+json` responses by
default; pass `problem_details=False` to keep the legacy error format.
Pass `problem_handler=` to enrich those payloads; request IDs are included when
`request_id=True` or structured logging is enabled. OpenAPI documents the shared
`ProblemDetails` schema, and generated clients expose it as `APIError.problem`.

## Documentation

https://responder.kennethreitz.org
