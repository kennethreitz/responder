Feature Tour
============

This section walks through Responder's features in detail. Each section
includes working code examples you can copy into your application.


Method Filtering
----------------

By default, a route matches all HTTP methods. If you want to restrict a
route to specific methods, pass the ``methods`` parameter::

    @api.route("/items", methods=["GET"])
    def list_items(req, resp):
        resp.media = {"items": []}

    @api.route("/items", methods=["POST"], check_existing=False)
    async def create_item(req, resp):
        data = await req.media()
        resp.media = {"created": data}

Note the ``check_existing=False`` — this allows you to register multiple
handlers for the same path with different methods.


Class-Based Views
-----------------

For more complex resources, you can use class-based views. Responder will
dispatch to the appropriate method handler based on the HTTP method::

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


Lifespan Events
---------------

Modern applications often need to set up resources on startup (database
connections, caches, ML models) and tear them down on shutdown. Responder
supports the lifespan context manager pattern::

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app):
        # Startup — runs before the first request
        print("connecting to database...")
        yield
        # Shutdown — runs after the server stops
        print("closing connections...")

    api = responder.API(lifespan=lifespan)

You can also use the traditional event decorator style::

    @api.on_event("startup")
    async def startup():
        print("starting up")

    @api.on_event("shutdown")
    async def shutdown():
        print("shutting down")

The context manager approach is preferred for new code — it makes the
startup/shutdown relationship explicit and keeps related code together.


Serving Files
-------------

Serve files from disk with automatic content-type detection. Responder
uses Python's ``mimetypes`` module to figure out the right ``Content-Type``
header for you::

    @api.route("/download")
    def download(req, resp):
        resp.file("reports/annual.pdf")

You can override the content type if needed::

    @api.route("/image")
    def image(req, resp):
        resp.file("photos/cat.jpg", content_type="image/jpeg")


Custom Error Handling
---------------------

By default, unhandled exceptions result in a 500 Internal Server Error.
You can register custom handlers for specific exception types to return
structured error responses::

    @api.exception_handler(ValueError)
    async def handle_value_error(req, resp, exc):
        resp.status_code = 400
        resp.media = {"error": str(exc)}

Now, any route that raises a ``ValueError`` will return a clean 400 response
with a JSON error message instead of a generic 500 page.


Before-Request Hooks
--------------------

Run code before every request. This is useful for logging, adding common
headers, or setting up per-request state::

    @api.route(before_request=True)
    def add_headers(req, resp):
        resp.headers["X-API-Version"] = "3.1"

**Short-circuiting:** If your hook sets ``resp.status_code``, the route
handler will be skipped entirely and the response will be sent immediately.
This is the pattern for authentication guards::

    @api.route(before_request=True)
    def auth_check(req, resp):
        if "Authorization" not in req.headers:
            resp.status_code = 401
            resp.media = {"error": "unauthorized"}

If the ``Authorization`` header is missing, the client gets a 401 response
and the actual route handler never runs.

WebSocket hooks work the same way::

    @api.before_request(websocket=True)
    async def ws_auth(ws):
        await ws.accept()


WebSocket Support
-----------------

Responder supports WebSockets for real-time, bidirectional communication::

    @api.route("/ws", websocket=True)
    async def websocket(ws):
        await ws.accept()
        while True:
            name = await ws.receive_text()
            await ws.send_text(f"Hello {name}!")
        await ws.close()

You can send and receive in multiple formats:

- ``send_text`` / ``receive_text`` — plain text
- ``send_json`` / ``receive_json`` — JSON objects
- ``send_bytes`` / ``receive_bytes`` — raw binary data


GraphQL
-------

Responder includes built-in GraphQL support via
`Graphene <https://graphene-python.org/>`_. Set up a full GraphQL endpoint
with a single method call::

    import graphene

    class Query(graphene.ObjectType):
        hello = graphene.String(name=graphene.String(default_value="stranger"))

        def resolve_hello(self, info, name):
            return f"Hello {name}"

    api.graphql("/graphql", schema=graphene.Schema(query=Query))

Visiting ``/graphql`` in a browser renders the GraphiQL interactive IDE,
where you can explore your schema and test queries. Programmatic clients
can POST JSON queries to the same endpoint.

You can access the Responder request and response objects in your resolvers
through ``info.context["request"]`` and ``info.context["response"]``.


OpenAPI Documentation
---------------------

Responder can generate an OpenAPI schema and serve interactive API
documentation automatically::

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

**Pydantic models** — the recommended approach for new APIs. Use
``request_model`` and ``response_model`` to annotate your routes, and
Responder will generate the schema automatically::

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

This generates a full OpenAPI path with ``requestBody`` and ``responses``
schemas, all linked by ``$ref`` to your Pydantic models in
``components/schemas``.

You can also register standalone schemas with the ``@api.schema`` decorator::

    @api.schema("Pet")
    class Pet(BaseModel):
        name: str
        age: int = 0

**YAML docstrings** — inline your OpenAPI spec directly in the docstring.
This gives you full control over every detail::

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

**Marshmallow schemas** — if you're already using marshmallow for
validation, Responder integrates with it via the apispec plugin::

    from marshmallow import Schema, fields

    @api.schema("Pet")
    class PetSchema(Schema):
        name = fields.Str()

All three approaches can be mixed in the same API. Pydantic models,
marshmallow schemas, and YAML docstrings all contribute to the same
generated OpenAPI specification.

You can choose from multiple documentation themes:
``swagger_ui`` (default), ``redoc``, ``rapidoc``, or ``elements``.


Mounting Other Apps
-------------------

Responder can mount any WSGI or ASGI application at a subroute. This means
you can gradually migrate from Flask, or run multiple frameworks side by side::

    from flask import Flask

    flask_app = Flask(__name__)

    @flask_app.route("/")
    def hello():
        return "Hello from Flask!"

    api.mount("/flask", flask_app)

Requests to ``/flask/`` will be handled by Flask. Everything else goes
through Responder. Both WSGI and ASGI apps are supported — Responder
wraps WSGI apps automatically.


Cookies
-------

Reading and writing cookies is straightforward::

    # Read cookies from the request
    session_id = req.cookies.get("session_id")

    # Set a cookie on the response
    resp.cookies["hello"] = "world"

For more control over cookie directives, use ``set_cookie``::

    resp.set_cookie(
        "token",
        value="abc123",
        max_age=3600,
        secure=True,
        httponly=True,
        path="/",
    )

Supported directives: ``key``, ``value``, ``expires``, ``max_age``,
``domain``, ``path``, ``secure``, ``httponly``.


Cookie-Based Sessions
---------------------

Responder has built-in support for signed, cookie-based sessions. Just
read from and write to the ``session`` dictionary::

    @api.route("/login")
    def login(req, resp):
        resp.session["username"] = "alice"

    @api.route("/profile")
    def profile(req, resp):
        resp.media = {"user": req.session.get("username")}

The session data is stored in a cookie called ``Responder-Session``. It's
signed for tamper protection, so you can trust that the data originated
from your server.

.. warning::

   For production use, always set a secret key::

       api = responder.API(secret_key="your-secret-key-here")


Static Files
------------

Static files are served from the ``static/`` directory by default::

    api = responder.API(static_dir="static", static_route="/static")

Place your CSS, JavaScript, images, and other assets in the ``static/``
directory and they'll be served automatically.

For single-page applications, you can serve ``index.html`` as the default
response for all unmatched routes::

    api.add_route("/", static=True)

You can add additional static directories at runtime::

    api.static_app.add_directory("extra_assets")


CORS
----

Enable Cross-Origin Resource Sharing for your API::

    api = responder.API(cors=True, cors_params={
        "allow_origins": ["https://example.com"],
        "allow_methods": ["GET", "POST"],
        "allow_headers": ["*"],
        "allow_credentials": True,
        "max_age": 600,
    })

The default CORS policy is restrictive — you must explicitly enable the
origins, methods, and headers your frontend needs.


HSTS
----

Force all traffic to HTTPS with a single flag::

    api = responder.API(enable_hsts=True)

This adds the ``Strict-Transport-Security`` header and redirects HTTP
requests to HTTPS.


Trusted Hosts
-------------

Protect against HTTP Host header attacks by restricting which hostnames
your application will respond to::

    api = responder.API(allowed_hosts=["example.com", "*.example.com"])

Requests with a ``Host`` header that doesn't match any of the patterns
will receive a 400 Bad Request response. Wildcard domains are supported.

By default, all hostnames are allowed.


Server-Sent Events (SSE)
------------------------

Stream real-time updates to the client using Server-Sent Events. This is
great for live feeds, progress updates, and AI streaming responses::

    @api.route("/events")
    async def events(req, resp):
        @resp.sse
        async def stream():
            for i in range(10):
                yield {"data": f"message {i}"}

Each yielded value can be a string (treated as data) or a dict with
``data``, ``event``, ``id``, and ``retry`` fields::

    yield {"event": "update", "data": "hello", "id": "1"}
    yield "simple string message"


Streaming Files
---------------

For large files, use ``resp.stream_file()`` to stream the content without
loading the entire file into memory::

    @api.route("/download")
    def download(req, resp):
        resp.stream_file("large-dataset.csv")

For small files where memory isn't a concern, ``resp.file()`` loads the
entire file at once — simpler but less efficient for large files.


After-Request Hooks
-------------------

Run code after every request, useful for logging, adding headers, or
cleanup::

    @api.after_request()
    def log_response(req, resp):
        print(f"{req.method} {req.full_url} -> {resp.status_code}")


Route Groups
------------

Organize related routes with a shared URL prefix. Useful for API versioning
and logical grouping::

    v1 = api.group("/v1")

    @v1.route("/users")
    def list_users(req, resp):
        resp.media = []

    @v1.route("/users/{user_id:int}")
    def get_user(req, resp, *, user_id):
        resp.media = {"id": user_id}


Request ID
----------

Auto-generate unique request IDs for tracing and debugging. If the client
sends an ``X-Request-ID`` header, it's forwarded; otherwise a new UUID is
generated::

    api = responder.API(request_id=True)


Rate Limiting
-------------

Built-in token bucket rate limiter::

    from responder.ext.ratelimit import RateLimiter

    limiter = RateLimiter(requests=100, period=60)  # 100 req/min
    limiter.install(api)

When the limit is exceeded, clients receive a ``429 Too Many Requests``
response with ``Retry-After`` and ``X-RateLimit-Remaining`` headers.


MessagePack
-----------

In addition to JSON and YAML, Responder supports MessagePack for efficient
binary serialization::

    # Decode MessagePack request body
    data = await req.media("msgpack")

    # Content negotiation also works — clients can send
    # Accept: application/x-msgpack to receive MessagePack responses.
