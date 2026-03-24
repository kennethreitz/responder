Feature Tour
============

This section walks through Responder's features in depth. Each section
explains the concept, shows working code, and explains the design choices
behind it. If you're new to web development, this is a good place to learn
how modern web frameworks work under the hood.


Method Filtering
----------------

HTTP defines several *methods* (also called verbs) that describe what a
client wants to do with a resource. The most common are:

- ``GET`` — retrieve data
- ``POST`` — create something new
- ``PUT`` — replace something entirely
- ``PATCH`` — update part of something
- ``DELETE`` — remove something

By default, a Responder route matches all methods. This is fine for simple
endpoints, but REST APIs typically map different methods to different
operations. Use the ``methods`` parameter to restrict a route::

    @api.route("/items", methods=["GET"])
    def list_items(req, resp):
        resp.media = {"items": []}

    @api.route("/items", methods=["POST"], check_existing=False)
    async def create_item(req, resp):
        data = await req.media()
        resp.media = {"created": data}

Note the ``check_existing=False`` — Responder normally prevents you from
registering two routes with the same path (to catch typos). When you
intentionally want multiple handlers for the same path with different
methods, you need to opt in.


Class-Based Views
-----------------

Function-based views are great for simple endpoints, but sometimes you want
to group related HTTP methods together into a single resource. This is
where class-based views come in — a pattern popularized by
`Falcon <https://falconframework.org/>`_.

Responder dispatches to the appropriate method handler based on the HTTP
method::

    @api.route("/{greeting}")
    class GreetingResource:
        def on_get(self, req, resp, *, greeting):
            resp.text = f"{greeting}, world!"

        def on_post(self, req, resp, *, greeting):
            resp.media = {"received": greeting}

        def on_request(self, req, resp, *, greeting):
            """Called on EVERY request, before the method-specific handler."""
            resp.headers["X-Greeting"] = greeting

The ``on_request`` method is called for all HTTP methods, much like
middleware scoped to a single route. Method-specific handlers (``on_get``,
``on_post``, ``on_put``, ``on_delete``, etc.) are called after.

No inheritance required — just define a class with the right method names.
This is simpler than Django's ``View`` classes and more Pythonic than
framework-specific base classes.


Lifespan Events
---------------

Real applications need to set up resources when they start (database
connection pools, ML models, caches) and tear them down when they stop.
This is called the application *lifespan*.

The modern approach is the *context manager* pattern, where startup and
shutdown are two halves of the same block::

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app):
        # Startup — runs before the first request
        print("connecting to database...")
        yield
        # Shutdown — runs after the server stops
        print("closing connections...")

    api = responder.API(lifespan=lifespan)

Everything before ``yield`` runs at startup. Everything after runs at
shutdown. If startup fails, the server won't start. If shutdown raises,
it's logged but the server still exits.

The traditional event decorator style also works::

    @api.on_event("startup")
    async def startup():
        print("starting up")

    @api.on_event("shutdown")
    async def shutdown():
        print("shutting down")

The context manager is preferred for new code — it keeps related startup
and shutdown logic together and makes resource cleanup more explicit.


Serving Files
-------------

Web applications often need to serve files — downloads, reports, images.
Responder makes this simple with ``resp.file()``, which reads a file from
disk and sets the ``Content-Type`` header automatically using Python's
``mimetypes`` module::

    @api.route("/download")
    def download(req, resp):
        resp.file("reports/annual.pdf")

You can override the content type if the automatic detection isn't right::

    @api.route("/image")
    def image(req, resp):
        resp.file("photos/cat.jpg", content_type="image/jpeg")

For large files, use ``resp.stream_file()`` to avoid loading the entire
file into memory. This streams the file in chunks::

    @api.route("/export")
    def export(req, resp):
        resp.stream_file("data/export.csv")


Custom Error Handling
---------------------

In production, you don't want your users to see raw Python tracebacks.
Responder lets you register custom handlers for specific exception types,
so you can return clean, structured error responses::

    @api.exception_handler(ValueError)
    async def handle_value_error(req, resp, exc):
        resp.status_code = 400
        resp.media = {"error": str(exc)}

Now, any route that raises a ``ValueError`` will return a clean JSON
response with a 400 status code instead of a generic 500 error page.

This is a common pattern in API development — you define your own exception
classes for different error conditions, register handlers for each, and
your API always returns consistent, machine-readable error responses.


Before-Request Hooks
--------------------

Sometimes you need to run the same code before every request —
authentication checks, request logging, adding common headers, or setting
up per-request state. Before-request hooks let you do this without
duplicating code in every route::

    @api.route(before_request=True)
    def add_headers(req, resp):
        resp.headers["X-API-Version"] = "3.2"

**Short-circuiting** is the really powerful part. If your hook sets
``resp.status_code``, the route handler is skipped entirely and the
response is sent immediately. This is the pattern for authentication::

    @api.route(before_request=True)
    def auth_check(req, resp):
        if "Authorization" not in req.headers:
            resp.status_code = 401
            resp.media = {"error": "unauthorized"}

If the ``Authorization`` header is missing, the client gets a 401 response
and the actual route handler never runs. This is cleaner than adding
auth checks to every individual route.


After-Request Hooks
-------------------

The complement to before-request hooks. After-request hooks run after the
route handler completes but before the response is sent. They're useful
for logging, adding response headers, or any post-processing::

    @api.after_request()
    def log_response(req, resp):
        print(f"{req.method} {req.full_url} -> {resp.status_code}")

    @api.after_request()
    async def add_timing(req, resp):
        resp.headers["X-Served-By"] = "responder"


WebSocket Support
-----------------

HTTP is a request-response protocol — the client asks, the server answers.
But some applications need real-time, bidirectional communication: chat
apps, live dashboards, multiplayer games, collaborative editors.

`WebSockets <https://en.wikipedia.org/wiki/WebSocket>`_ solve this by
upgrading an HTTP connection into a persistent, full-duplex channel where
both sides can send messages at any time::

    @api.route("/ws", websocket=True)
    async def websocket(ws):
        await ws.accept()
        while True:
            name = await ws.receive_text()
            await ws.send_text(f"Hello {name}!")
        await ws.close()

You can send and receive in multiple formats:

- ``send_text`` / ``receive_text`` — plain text strings
- ``send_json`` / ``receive_json`` — JSON objects (auto-serialized)
- ``send_bytes`` / ``receive_bytes`` — raw binary data

WebSocket routes are marked with ``websocket=True`` in the route decorator.
They receive a ``ws`` object instead of ``req`` and ``resp``.


Server-Sent Events (SSE)
-------------------------

SSE is a simpler alternative to WebSockets for *one-way* real-time
communication — the server pushes events to the client, but the client
can't send messages back. This is perfect for live feeds, progress bars,
notification streams, and AI response streaming.

Unlike WebSockets, SSE works over plain HTTP, is automatically reconnected
by the browser, and doesn't require any special client-side libraries::

    @api.route("/events")
    async def events(req, resp):
        @resp.sse
        async def stream():
            for i in range(10):
                yield {"data": f"message {i}"}

On the client side, you consume SSE events with JavaScript's built-in
``EventSource`` API::

    const source = new EventSource("/events");
    source.onmessage = (event) => {
        console.log(event.data);
    };

Each yielded value can be a string (treated as data) or a dict with the
standard SSE fields::

    yield {"event": "update", "data": "hello", "id": "1", "retry": "5000"}
    yield "simple string message"


GraphQL
-------

`GraphQL <https://graphql.org/>`_ is a query language for APIs that lets
clients request exactly the data they need — no more, no less. Instead of
multiple REST endpoints, you define a schema and let clients query it.

Responder includes built-in GraphQL support via
`Graphene <https://graphene-python.org/>`_. Set up a full GraphQL endpoint
with a single method call::

    import graphene

    class Query(graphene.ObjectType):
        hello = graphene.String(name=graphene.String(default_value="stranger"))

        def resolve_hello(self, info, name):
            return f"Hello {name}"

    api.graphql("/graphql", schema=graphene.Schema(query=Query))

Visiting ``/graphql`` in a browser renders the
`GraphiQL <https://github.com/graphql/graphiql>`_ interactive IDE, where
you can explore your schema, write queries, and see results in real-time.
Programmatic clients can POST JSON queries to the same endpoint.

You can access the Responder request and response objects in your resolvers
through ``info.context["request"]`` and ``info.context["response"]``.


OpenAPI Documentation
---------------------

`OpenAPI <https://www.openapis.org/>`_ (formerly Swagger) is the industry
standard for describing REST APIs. An OpenAPI specification lets you
auto-generate interactive documentation, client libraries, and validation
logic.

Responder generates OpenAPI specs from your code::

    api = responder.API(
        title="Pet Store",
        version="1.0",
        openapi="3.0.2",
        docs_route="/docs",
    )

This gives you:

- An OpenAPI schema at ``/schema.yml``
- Interactive Swagger UI documentation at ``/docs``

There are three ways to document your endpoints.

**Pydantic models** — the recommended approach. Use ``request_model`` and
``response_model`` to annotate your routes, and Responder generates the
schema automatically. When ``request_model`` is set, request bodies are
also validated automatically — invalid inputs get a ``422`` response with
detailed error messages::

    from pydantic import BaseModel

    class PetIn(BaseModel):
        name: str
        age: int = 0

    class PetOut(BaseModel):
        id: int
        name: str
        age: int

    @api.route("/pets", methods=["POST"],
               request_model=PetIn, response_model=PetOut)
    async def create_pet(req, resp):
        data = await req.media()
        resp.media = {"id": 1, **data}

When ``response_model`` is set, the response is serialized through the
model — extra fields are stripped and types are enforced.

**YAML docstrings** — for full control, embed OpenAPI YAML in the
docstring::

    @api.route("/pets")
    def list_pets(req, resp):
        """A list of pets.
        ---
        get:
            description: Get all pets
            responses:
                200:
                    description: A list of pets
        """
        resp.media = [{"name": "Fido"}]

**Marshmallow schemas** — if you're already using marshmallow::

    from marshmallow import Schema, fields

    @api.schema("Pet")
    class PetSchema(Schema):
        name = fields.Str()

All three approaches can be mixed in the same API. You can choose from
multiple documentation themes: ``swagger_ui`` (default), ``redoc``,
``rapidoc``, or ``elements``.


Route Groups
------------

As your application grows, you'll want to organize routes logically.
Route groups let you share a URL prefix across related endpoints — a
common pattern for API versioning::

    v1 = api.group("/v1")

    @v1.route("/users")
    def list_users(req, resp):
        resp.media = []

    @v1.route("/users/{user_id:int}")
    def get_user(req, resp, *, user_id):
        resp.media = {"id": user_id}

    v2 = api.group("/v2")

    @v2.route("/users")
    def list_users_v2(req, resp):
        resp.media = {"users": [], "total": 0}

This keeps your code organized without affecting the routing logic.


Mounting Other Apps
-------------------

Responder can mount any WSGI or ASGI application at a subroute. This is
incredibly useful for gradual migrations — you can run Flask and Responder
side by side, moving routes over one at a time::

    from flask import Flask

    flask_app = Flask(__name__)

    @flask_app.route("/")
    def hello():
        return "Hello from Flask!"

    api.mount("/flask", flask_app)

Requests to ``/flask/`` will be handled by Flask. Everything else goes
through Responder. Both WSGI and ASGI apps are supported — Responder
wraps WSGI apps in an ASGI adapter automatically.

You can also mount `marimo <https://marimo.io/>`_ notebooks as
interactive dashboards within your API::

    import marimo

    server = (
        marimo.create_asgi_app()
        .with_app(path="", root="./notebooks/dashboard.py")
        .with_app(path="/analysis", root="./notebooks/analysis.py")
    )

    api.mount("/notebooks", server.build())

Notebooks are served at ``/notebooks/`` and ``/notebooks/analysis``,
with full interactivity — reactive cells, widgets, plots, and all.


Cookies
-------

`Cookies <https://developer.mozilla.org/en-US/docs/Web/HTTP/Cookies>`_ are
small pieces of data that the server asks the browser to store and send
back with every subsequent request. They're the foundation of sessions,
authentication tokens, and user preferences on the web.

Reading and writing cookies is straightforward::

    # Read cookies from the request
    session_id = req.cookies.get("session_id")

    # Set a cookie on the response
    resp.cookies["hello"] = "world"

For production use, you'll want to set security directives. The
``httponly`` flag prevents JavaScript from reading the cookie (defending
against XSS attacks), and ``secure`` ensures it's only sent over HTTPS::

    resp.set_cookie(
        "token",
        value="abc123",
        max_age=3600,        # expires in 1 hour
        secure=True,         # HTTPS only
        httponly=True,        # no JavaScript access
        path="/",
    )


Cookie-Based Sessions
---------------------

Sessions let you store per-user data across multiple requests. Responder's
built-in sessions are cookie-based — the session data is serialized, signed
with your secret key, and stored in a cookie. The signature prevents
tampering: if someone modifies the cookie, the signature won't match and
the data will be rejected::

    @api.route("/login")
    def login(req, resp):
        resp.session["username"] = "alice"

    @api.route("/profile")
    def profile(req, resp):
        resp.media = {"user": req.session.get("username")}

.. warning::

   Always set a secret key in production. The default key is not secret::

       api = responder.API(secret_key="your-secret-key-here")


Static Files
------------

Most web applications serve static assets — CSS stylesheets, JavaScript
files, images, fonts. Responder serves these from the ``static/`` directory
by default::

    api = responder.API(static_dir="static", static_route="/static")

Place your assets in the ``static/`` directory and they'll be served
automatically at ``/static/style.css``, ``/static/app.js``, etc.

For single-page applications (React, Vue, Angular), you can serve
``index.html`` as the default response for all unmatched routes::

    api.add_route("/", static=True)


CORS
----

`CORS <https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS>`_ (Cross-
Origin Resource Sharing) is a security mechanism that controls which
websites can make requests to your API. Browsers enforce this — if your
API is at ``api.example.com`` and your frontend is at ``app.example.com``,
the browser will block requests unless your API explicitly allows it.

Enable CORS and configure which origins are allowed::

    api = responder.API(cors=True, cors_params={
        "allow_origins": ["https://app.example.com"],
        "allow_methods": ["GET", "POST"],
        "allow_headers": ["*"],
        "allow_credentials": True,
        "max_age": 600,
    })

The default policy is restrictive — you must explicitly allow each origin.
Using ``["*"]`` for allow_origins permits any website to call your API,
which is fine for public APIs but not for private ones.


HSTS
----

`HSTS <https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Strict-Transport-Security>`_
(HTTP Strict Transport Security) tells browsers to always use HTTPS when
communicating with your server. Once a browser sees the HSTS header, it
will refuse to connect over plain HTTP, even if the user types ``http://``
in the address bar::

    api = responder.API(enable_hsts=True)


Trusted Hosts
-------------

The ``Host`` header in an HTTP request tells the server which domain name
the client used. Attackers can forge this header to trick your application
into generating URLs to malicious domains (a class of attack called *Host
header injection*).

Restrict which hostnames your application accepts::

    api = responder.API(allowed_hosts=["example.com", "*.example.com"])

Requests with unrecognized hosts get a ``400 Bad Request``. Wildcard
patterns are supported. By default, all hostnames are allowed.


Request ID
----------

In distributed systems, tracing a single request across multiple services
is essential for debugging. Request IDs are unique identifiers attached to
each request — if something goes wrong, you can search your logs for that
ID and find every related event.

Responder can auto-generate request IDs. If the client sends an
``X-Request-ID`` header (common in microservice architectures), it's
forwarded. Otherwise, a new UUID is generated::

    api = responder.API(request_id=True)

The ID appears in the ``X-Request-ID`` response header.


Rate Limiting
-------------

Rate limiting prevents individual clients from overwhelming your API with
too many requests. It's essential for public APIs, and good practice even
for internal ones.

Responder includes a built-in token bucket rate limiter::

    from responder.ext.ratelimit import RateLimiter

    limiter = RateLimiter(requests=100, period=60)  # 100 req/min
    limiter.install(api)

When the limit is exceeded, clients receive a ``429 Too Many Requests``
response with a ``Retry-After`` header. Every response includes
``X-RateLimit-Limit`` and ``X-RateLimit-Remaining`` headers so clients
can pace themselves.

The rate limiter is per-client, keyed by IP address.


Structured Logging
------------------

Production applications need structured, searchable logs. Responder
includes built-in logging that automatically attaches request context
— request ID, HTTP method, path, and client IP — to every log message
emitted during request handling::

    api = responder.API(enable_logging=True)

This gives you:

- **Access logging** with timing for every request::

    2026-03-24 12:00:00 [INFO] responder.access — GET /users → 200 (1.2ms)

- **A logger on the API instance** — use ``api.log`` anywhere in
  your routes. Request context (ID, method, path, client IP) is
  attached automatically::

    @api.route("/users/{user_id:int}")
    def get_user(req, resp, *, user_id):
        api.log.info("fetching user %d", user_id)
        # => [INFO] responder.app -- fetching user 42 [GET /users/42] [req:a1b2c3] [client:10.0.0.1]
        resp.media = {"id": user_id}

- **Request IDs** generated automatically (or forwarded from the
  ``X-Request-ID`` header) and included in responses.

The logging uses Python's standard ``logging`` module, so it works with
any handler — files, syslog, JSON formatters, Datadog, Sentry, whatever
you already use.

For additional loggers (e.g. in helper modules), use ``get_logger``::

    from responder.ext.logging import get_logger
    logger = get_logger("myapp.db")

You can also access the current request context directly::

    from responder.ext.logging import RequestContext

    @api.route("/debug")
    def debug(req, resp):
        resp.media = {
            "request_id": RequestContext.get_request_id(),
            "client_ip": RequestContext.get_client_ip(),
        }

When ``enable_logging=True`` is set, it supersedes ``request_id=True``
— the logging middleware handles request IDs itself, so you don't get
duplicate headers.


Pydantic Validation
-------------------

`Pydantic <https://docs.pydantic.dev/>`_ models integrate directly with
Responder's routing. Set ``request_model`` to validate incoming data and
``response_model`` to control the shape of outgoing data::

    from pydantic import BaseModel

    class ItemIn(BaseModel):
        name: str
        price: float

    class ItemOut(BaseModel):
        id: int
        name: str
        price: float

    @api.route("/items", methods=["POST"],
               request_model=ItemIn, response_model=ItemOut)
    async def create_item(req, resp):
        data = await req.media()
        resp.media = {"id": 1, **data}

When ``request_model`` is set:

- Valid requests are parsed and the data is available via ``await req.media()``
- Invalid requests get an automatic ``422 Unprocessable Entity`` response
  with detailed error messages — you don't write any validation code

When ``response_model`` is set:

- The response is serialized through the model before being sent
- Extra fields are stripped automatically
- Type coercion happens at the boundary

This is the recommended way to build validated REST APIs with Responder.
See the :doc:`tutorial-rest` for a complete walkthrough.


Content Negotiation
-------------------

Responder automatically negotiates the response format based on the
client's ``Accept`` header. Set ``resp.media`` to a Python object and
the right thing happens:

- ``Accept: application/json`` (default) → JSON
- ``Accept: application/x-yaml`` → YAML
- ``Accept: application/x-msgpack`` → MessagePack

This means a single endpoint serves multiple formats without any
conditional logic in your code::

    @api.route("/data")
    def data(req, resp):
        resp.media = {"key": "value"}

Clients get the format they ask for::

    $ curl http://localhost:5042/data
    {"key": "value"}

    $ curl -H "Accept: application/x-yaml" http://localhost:5042/data
    key: value


MessagePack
-----------

`MessagePack <https://msgpack.org/>`_ is a binary serialization format
that's more compact and faster to parse than JSON. It's useful for
high-throughput APIs, IoT devices, and anywhere bandwidth matters.

Responder supports MessagePack alongside JSON and YAML::

    # Decode a MessagePack request body
    data = await req.media("msgpack")

    # Respond with MessagePack
    resp.media = {"result": [1, 2, 3]}

Content negotiation works automatically — clients can send
``Accept: application/x-msgpack`` to receive MessagePack responses
instead of JSON. You can also explicitly decode MessagePack request
bodies by passing ``"msgpack"`` to ``req.media()``.
