<h1 align="center">Responder</h1>

<p align="center">
  <strong>Web services for humans.</strong><br>
  A familiar HTTP Service Framework for Python, powered by Starlette.
</p>

<p align="center">
  <a href="https://pypi.org/project/responder/"><img alt="PyPI" src="https://img.shields.io/pypi/v/responder.svg"></a>
  <a href="https://pypi.org/project/responder/"><img alt="Python versions" src="https://img.shields.io/pypi/pyversions/responder.svg"></a>
  <a href="https://github.com/kennethreitz/responder/actions/workflows/test.yaml"><img alt="Tests" src="https://github.com/kennethreitz/responder/actions/workflows/test.yaml/badge.svg"></a>
  <a href="https://github.com/kennethreitz/responder/actions/workflows/lint.yaml"><img alt="Lint and types" src="https://github.com/kennethreitz/responder/actions/workflows/lint.yaml/badge.svg"></a>
  <a href="https://responder.kennethreitz.org"><img alt="Documentation" src="https://github.com/kennethreitz/responder/actions/workflows/docs.yaml/badge.svg"></a>
  <a href="https://pypi.org/project/responder/"><img alt="License" src="https://img.shields.io/pypi/l/responder.svg"></a>
</p>

<p align="center">
  <a href="https://responder.kennethreitz.org">Documentation</a>
  · <a href="https://responder.kennethreitz.org/quickstart.html">Quickstart</a>
  · <a href="https://responder.kennethreitz.org/tour.html">Tour</a>
  · <a href="https://responder.kennethreitz.org/examples.html">Examples</a>
  · <a href="https://github.com/kennethreitz/responder/blob/main/CHANGELOG.md">Changelog</a>
</p>

```python
import responder

api = responder.API()


@api.get("/hello/{name}")
def hello(req, resp, *, name):
    resp.media = {"hello": name}


if __name__ == "__main__":
    api.run()
```

```console
$ pip install responder
$ python app.py
```

Open `http://127.0.0.1:5042/hello/world`. That's it.

Responder is the friendly request/response shape of Flask and Falcon, brought
to ASGI with Starlette underneath. Every view receives a `req` and a `resp`.
Read from one, write to the other. Sync and async views both work.

## Why Responder?

Responder is for people who like small, expressive web frameworks with real
batteries included.

| You want | Responder gives you |
| --- | --- |
| A simple mental model | `def view(req, resp): ...` with mutable request and response objects |
| Modern Python I/O | ASGI, Starlette routing, uvicorn by default, optional Granian |
| Real API contracts | Pydantic request/response models and generated OpenAPI 3.0/3.1 |
| Pleasant responses | `resp.text`, `resp.html`, `resp.media`, `resp.file()`, `resp.problem()` |
| Production ergonomics | request IDs, structured logging, rate limiting, health checks, metrics |
| Safer defaults | Problem Details errors, capped request bodies, secure session guidance |
| Escape hatches | mount Flask, Django, WSGI, ASGI apps, or plain routers under one API |

## The Shape

Routes look like Python strings, because they are meant to be read.

```python
@api.get("/users/{user_id:int}")
def get_user(req, resp, *, user_id):
    resp.media = {"id": user_id, "name": "Ada"}
```

Write the response you mean:

```python
resp.text = "hello"
resp.html = "<h1>Hello</h1>"
resp.media = {"ok": True}       # JSON by default, YAML/msgpack by negotiation
resp.file("report.pdf")         # content type detected for you
resp.status_code = 201
resp.headers["Location"] = "/items/1"
```

Read requests without ceremony:

```python
@api.post("/echo")
async def echo(req, resp):
    payload = await req.media()
    resp.media = {"you_sent": payload}
```

Return values work too, when that style feels right:

```python
@api.get("/ping")
def ping(req, resp):
    return {"pong": True}
```

## A Real Endpoint

Responder can stay tiny, but it does not stop at toy apps.

```python
from pydantic import BaseModel, Field

import responder
from responder.ext.auth import BearerAuth


class ItemIn(BaseModel):
    name: str = Field(min_length=1)
    price: float = Field(gt=0)


class ItemOut(ItemIn):
    id: int


class User(BaseModel):
    name: str
    scopes: list[str]


api = responder.API(
    title="Store API",
    version="1.0",
    openapi="3.1.0",
    docs_route="/docs",
    request_id=True,
)

users = {"secret-token": User(name="Ada", scopes=["items:write"])}
auth = BearerAuth(verify=lambda token: users.get(token), bearer_format="opaque")
writer = api.policy("writer", auth.requires("items:write"))


@api.post(
    "/items",
    auth=writer,
    response_model=ItemOut,
    status_code=201,
    summary="Create an item",
)
def create_item(req, resp, *, item: ItemIn, user):
    resp.media = ItemOut(id=1, **item.model_dump())
```

You get validation, auth enforcement, a documented request body, a documented
response body, `401`/`403`/`422` Problem Details responses, request IDs, and
Swagger UI at `/docs`.

## What's Included

| Area | Highlights |
| --- | --- |
| Routing | `@api.get`, `@api.post`, route groups, class-based views, typed path convertors |
| Validation | Pydantic body models, query/header/cookie markers, typed response models |
| OpenAPI | OpenAPI 3.0/3.1, Swagger UI, examples, security schemes, generated clients |
| Responses | JSON/YAML/msgpack negotiation, files, streaming, SSE, byte ranges, ETags |
| Security | signed sessions, server-side sessions, CSRF protection, auth helpers, JWT/OAuth2 |
| Operations | request IDs, structured access logs, health checks, Prometheus metrics |
| Limits | request body caps, streaming multipart uploads, in-memory/Redis rate limiting |
| Composition | dependencies with teardown, background tasks, WSGI/ASGI mounting, WebSockets |
| Deployment | built-in uvicorn runner, optional Granian, proxy-header support |
| Testing | in-process `api.requests` and configurable `api.test_client(...)` |

## v9 Highlights

Responder 9 tightened the production story while keeping the familiar API:

- Multipart uploads stream from the wire and spool to disk instead of buffering
  entire files in memory.
- Request bodies are capped at 100 MiB by default; pass
  `API(max_request_size=None)` for the legacy unlimited behavior.
- `API(csrf=True)` adds session-bound CSRF protection for unsafe requests, with
  per-route opt-outs for webhooks.
- `API(trust_proxy_headers=True)` rewrites scheme, host, and client IP from
  trusted reverse-proxy headers.
- Framework-generated errors use RFC 9457-style `application/problem+json`
  responses by default.
- OpenAPI documents operational responses such as CSRF `403`, body-cap `413`,
  rate-limit `429`, fail-closed limiter `503`, validation `422`, and timeout
  `504` where they can actually happen.

Upgrading from an earlier major version? Start with the
[v9 migration guide](https://responder.kennethreitz.org/migration-v9.html).

## Installation

```console
$ pip install responder
```

Python 3.11 and newer are supported.

Optional extras:

```console
$ pip install "responder[server]"   # Granian production server
$ pip install "responder[graphql]"  # GraphQL with Graphene
$ pip install "responder[jwt]"      # JWT auth helpers
$ pip install "responder[orjson]"   # orjson JSON backend
```

With `uv`:

```console
$ uv add responder
```

## Run It

```python
# app.py
import responder

api = responder.API()


@api.get("/")
def index(req, resp):
    resp.text = "hello, world!"


if __name__ == "__main__":
    api.run(port=8000)
```

```console
$ python app.py
```

Or through the CLI:

```console
$ responder run app.py
```

## OpenAPI and Clients

Turn on OpenAPI with two arguments:

```python
api = responder.API(
    title="Acme API",
    version="1.0",
    openapi="3.1.0",
    docs_route="/docs",
)
```

Responder builds the schema from routes, type hints, Pydantic models, auth
helpers, and framework behavior. The docs UI appears at `/docs`, the schema at
`/schema.yml`, and client code can be generated for Python, JavaScript,
TypeScript, Ruby, and PHP.

```console
$ responder client --class-name StoreClient --output store_client.py app:api
```

## Examples Worth Reading

| Example | Why it is useful |
| --- | --- |
| [`examples/atelier.py`](examples/atelier.py) | The golden contract app: auth, policies, examples, OpenAPI, generated-client coverage |
| [`examples/todo.py`](examples/todo.py) | A practical typed Todo API with protected writes and polished schema metadata |
| [`examples/fortunes.py`](examples/fortunes.py) | Tiny app wrapping the local `fortune` CLI tool |
| [`examples/tarot.py`](examples/tarot.py) | A playful API that shuffles, lists, and deals tarot cards |
| [`examples/sse_stream.py`](examples/sse_stream.py) | Server-Sent Events and streaming responses |
| [`examples/websocket_chat.py`](examples/websocket_chat.py) | WebSocket chat with Responder's route style |
| [`examples/marimo_mount.py`](examples/marimo_mount.py) | Mounting a marimo notebook app under Responder |

Run most examples with:

```console
$ responder run examples/todo.py
```

## Philosophy

Responder is intentionally familiar. If you know Flask, Falcon, Requests, or
Starlette, you already know most of the ideas. The framework tries to make the
simple thing feel natural, then keeps enough power nearby for real services:
typed contracts, OpenAPI, auth, rate limiting, streaming uploads, websockets,
and production middleware.

It is a passion project and a practical toolkit. It is especially good for
personal services, internal tools, prototypes, teaching, research apps, and
small APIs where clarity matters more than ceremony.

## Documentation

The full guide lives at [responder.kennethreitz.org](https://responder.kennethreitz.org).

- [Quickstart](https://responder.kennethreitz.org/quickstart.html)
- [Tour](https://responder.kennethreitz.org/tour.html)
- [Examples](https://responder.kennethreitz.org/examples.html)
- [Deployment](https://responder.kennethreitz.org/deployment.html)
- [Testing](https://responder.kennethreitz.org/testing.html)
- [API reference](https://responder.kennethreitz.org/api.html)
- [Changelog](CHANGELOG.md)

## License

Apache-2.0.
