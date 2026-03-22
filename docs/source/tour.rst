Feature Tour
============


Method Filtering
----------------

Restrict routes to specific HTTP methods::

    @api.route("/items", methods=["GET"])
    def list_items(req, resp):
        resp.media = {"items": []}

    @api.route("/items", methods=["POST"], check_existing=False)
    async def create_item(req, resp):
        data = await req.media()
        resp.media = {"created": data}


Class-Based Views
-----------------

::

    @api.route("/{greeting}")
    class GreetingResource:
        def on_get(self, req, resp, *, greeting):
            resp.text = f"{greeting}, world!"

        def on_post(self, req, resp, *, greeting):
            resp.media = {"received": greeting}

        def on_request(self, req, resp, *, greeting):
            """Called on every request method."""
            resp.headers["X-Greeting"] = greeting


Lifespan Events
---------------

Use a context manager for startup and shutdown::

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app):
        # Startup
        print("connecting to database...")
        yield
        # Shutdown
        print("closing connections...")

    api = responder.API(lifespan=lifespan)

Or use event decorators::

    @api.on_event("startup")
    async def startup():
        print("starting up")

    @api.on_event("shutdown")
    async def shutdown():
        print("shutting down")


File Serving
------------

Serve files with automatic content-type detection::

    @api.route("/download")
    def download(req, resp):
        resp.file("reports/annual.pdf")

    @api.route("/image")
    def image(req, resp):
        resp.file("photos/cat.jpg", content_type="image/jpeg")


Error Handling
--------------

Register handlers for specific exception types::

    @api.exception_handler(ValueError)
    async def handle_value_error(req, resp, exc):
        resp.status_code = 400
        resp.media = {"error": str(exc)}


Before-Request Hooks
--------------------

Run code before every request::

    @api.route(before_request=True)
    def add_headers(req, resp):
        resp.headers["X-API-Version"] = "3.1"

Short-circuit by setting a status code — the route handler will be skipped::

    @api.route(before_request=True)
    def auth_check(req, resp):
        if "Authorization" not in req.headers:
            resp.status_code = 401
            resp.media = {"error": "unauthorized"}


WebSockets
----------

::

    @api.route("/ws", websocket=True)
    async def websocket(ws):
        await ws.accept()
        while True:
            name = await ws.receive_text()
            await ws.send_text(f"Hello {name}!")
        await ws.close()

Supported formats: ``send_text``, ``send_json``, ``send_bytes``.


GraphQL
-------

One-liner setup with `Graphene <https://graphene-python.org/>`_::

    import graphene

    class Query(graphene.ObjectType):
        hello = graphene.String(name=graphene.String(default_value="stranger"))
        def resolve_hello(self, info, name):
            return f"Hello {name}"

    api.graphql("/graphql", schema=graphene.Schema(query=Query))

Visiting ``/graphql`` in a browser renders the GraphiQL IDE.


OpenAPI
-------

::

    api = responder.API(
        title="My API",
        version="1.0",
        openapi="3.0.2",
        docs_route="/docs",
    )

Visit ``/docs`` for interactive Swagger UI documentation.
The schema is served at ``/schema.yml``.


Mounting Apps
-------------

Mount any WSGI or ASGI application at a subroute::

    from flask import Flask

    flask_app = Flask(__name__)

    @flask_app.route("/")
    def hello():
        return "Hello from Flask!"

    api.mount("/flask", flask_app)


Cookies
-------

::

    # Read cookies
    req.cookies["session_id"]

    # Set cookies
    resp.cookies["hello"] = "world"

    # With directives
    resp.set_cookie("token", value="abc", max_age=3600, secure=True)


Sessions
--------

Built-in cookie-based sessions::

    @api.route("/login")
    def login(req, resp):
        resp.session["username"] = "alice"

    @api.route("/profile")
    def profile(req, resp):
        resp.media = {"user": req.session.get("username")}

Set a secret key for production::

    api = responder.API(secret_key="your-secret-key")


Static Files
------------

Static files are served from the ``static/`` directory by default::

    api = responder.API(static_dir="static", static_route="/static")

For single-page apps, serve ``index.html`` as the default::

    api.add_route("/", static=True)


CORS
----

::

    api = responder.API(cors=True, cors_params={
        "allow_origins": ["https://example.com"],
        "allow_methods": ["GET", "POST"],
        "allow_headers": ["*"],
    })


HSTS
----

Redirect all traffic to HTTPS::

    api = responder.API(enable_hsts=True)


Trusted Hosts
-------------

::

    api = responder.API(allowed_hosts=["example.com", "*.example.com"])
