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

Method-restricted routes get correct HTTP semantics for free:

- Requests with an unsupported method receive ``405 Method Not Allowed``
  (not 404) with an ``Allow`` header listing what the path supports.
- ``OPTIONS`` requests are answered automatically with the ``Allow`` header.
- ``HEAD`` is accepted wherever ``GET`` is.


Returning Values
----------------

Handlers normally communicate by mutating ``resp``, but they can also just
return a value::

    @api.route("/users")
    def list_users(req, resp):
        return [{"name": "alice"}]      # same as resp.media = [...]

    @api.route("/hello")
    def hello(req, resp):
        return "hello, world!"          # same as resp.text = "..."

A ``dict``, ``list``, number, or boolean becomes ``resp.media``, a ``str``
becomes ``resp.text``, and ``bytes`` become ``resp.content``. A Pydantic model
or a dataclass instance also becomes ``resp.media`` — serialized natively
across JSON, YAML, and MessagePack, so a trailing ``.model_dump()`` is optional.
Returning ``None`` (the implicit default) leaves the response exactly as you
set it, so existing handlers are unaffected. Use whichever style reads
better — for quick JSON endpoints, returning the data directly is hard to
beat.

To set a status code, or status and headers, return a Flask-style tuple —
``body, status`` or ``body, status, headers``::

    @api.route("/items/{id:int}")
    def get_item(req, resp, *, id):
        item = lookup(id)
        if item is None:
            return {"error": "not found"}, 404
        return item, 200, {"X-Source": "cache"}

Routes are also forgiving about trailing slashes: a request to ``/users/``
when only ``/users`` is registered (or vice versa) receives a ``307``
redirect to the canonical path, preserving the method and query string.
Pass ``redirect_slashes=False`` to ``API()`` for strict matching.


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

Everything the type-driven request pipeline offers function views works on
class-based view methods too: a required Pydantic-typed body parameter on a
write-method handler receives the parsed, validated body (an invalid body
returns ``422`` before the handler runs), and ``Query()``/``Header()``/
``Cookie()``/``Path()``/``Form()`` markers resolve exactly as they do on
functions::

    from pydantic import BaseModel
    from responder import Query

    class ItemIn(BaseModel):
        name: str
        price: float

    @api.route("/items")
    class ItemResource:
        def on_get(self, req, resp, *, limit: int = Query(10)):
            resp.media = fetch_items(limit)

        def on_post(self, req, resp, *, item: ItemIn):
            resp.media = create_item(item)

When ``on_request`` and a method handler both declare the same body model,
the body is parsed and validated once and the same instance is injected into
both.

Class-based views also answer ``OPTIONS`` automatically with ``200`` and an
``Allow`` header listing the implemented methods — define ``on_options``
(or a catch-all ``on_request``) to take over. A request for an unimplemented
method still gets a ``405`` whose ``Allow`` header advertises exactly the
methods that are actually served.


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

The two styles compose: with ``lifespan=`` set, ``on_event("startup")``
handlers run after the context manager is entered, and
``on_event("shutdown")`` handlers run before it exits.

For values that belong to the application as a whole, use ``api.state`` —
a free-form namespace reachable from any handler via ``req.api.state``::

    @api.on_event("startup")
    async def connect():
        api.state.db = await create_pool()

    @api.route("/users")
    async def users(req, resp):
        resp.media = await req.api.state.db.fetch_users()

(For resources with teardown, app-scoped dependencies below are usually
the better fit.)


Dependency Injection
--------------------

Views often need shared resources — a database session, a config object,
the current user. Rather than reaching for globals or recomputing them in
every handler, register them as *dependencies* and declare them as view
parameters::

    @api.dependency()
    async def db():
        session = await create_session()
        yield session
        await session.close()

    @api.route("/users/{id:int}")
    async def get_user(req, resp, *, id, db):
        resp.media = await db.fetch_user(id)

Any view parameter (beyond ``req`` and ``resp``) whose name matches a
registered dependency is injected automatically. Path parameters take
precedence over dependencies of the same name.

For one-off dependencies that belong to a single route, use ``Depends``
instead of registering a global name::

    from responder import Depends

    def current_user(req):
        return decode_user(req.headers.get("Authorization"))

    @api.route("/me")
    def me(req, resp, *, user=Depends(current_user)):
        resp.media = {"user": user}

``Depends`` providers follow the same lifecycle rules as registered
dependencies, including generator teardown.

``Depends`` also **nests**: a provider's own parameters can use ``Depends``, so
dependencies compose into chains without registering each one by name::

    def get_db():
        ...

    def current_user(db=Depends(get_db)):  # a provider depending on a provider
        return load_user(db)

    @api.route("/me")
    def me(req, resp, *, user=Depends(current_user)):
        resp.media = {"user": user}

Each provider resolves once per request and shares the same cache, and cycles
are detected and reported.

When a dependency is only a guard or setup step, attach it to the route instead
of adding an unused handler parameter::

    @api.route("/private", dependencies=[Depends(current_user)])
    def private(req, resp):
        resp.media = {"ok": True}

Providers can be:

- **Plain functions** (sync or async) — the return value is injected.
- **Generators** (sync or async) — the yielded value is injected, and the
  code after ``yield`` runs as teardown once the response is sent (for
  WebSocket routes, when the connection closes). This is perfect for
  database sessions and other resources that need cleanup. Teardown runs
  even if the handler raised.

To make a dependency request-aware, give the provider a parameter named
``req`` (or ``request``, or one annotated ``responder.Request``) — it
receives the current request::

    @api.dependency()
    def current_user(req):
        return decode_token(req.headers.get("Authorization", ""))

**Dependencies compose.** A provider can depend on other providers simply
by naming them as parameters; Responder resolves the whole graph for you,
memoizes each provider so it runs at most once per request, and tears
everything down in reverse order::

    @api.dependency()
    def config():
        return load_config()

    @api.dependency()
    async def db(config):                 # depends on `config`
        session = await create_session(config.db_url)
        yield session
        await session.close()

    @api.route("/users/{id:int}")
    async def get_user(req, resp, *, id, db):
        resp.media = await db.fetch_user(id)

Because resolution is memoized across the whole request, a shared provider
(or one asked for by both ``on_request`` and ``on_get`` in a class-based
view) runs a single time. Cyclic dependencies are detected and raise
``DependencyCycleError``.

The names ``req``, ``request``, ``resp``, ``response``, ``ws``, and
``websocket`` are reserved and can't be used as dependency names. When
resolution goes wrong, Responder raises a catchable ``DependencyError`` —
specifically ``DependencyCycleError``, ``DependencyScopeError``, or
``DependencyResolutionError``. To register under a different name, pass it
explicitly with ``@api.dependency(name="db")`` or call
``api.add_dependency("db", provider)``.

For resources that should live as long as the application — connection
pools, ML models, expensive clients — use the ``"app"`` scope. The provider
runs once, on first use, and generator teardown is deferred until the
application shuts down::

    @api.dependency(scope="app")
    async def pool():
        pool = await create_pool()
        yield pool
        await pool.close()   # runs at shutdown

App-scoped providers may compose with *other* app-scoped providers, but
they can't depend on the request or on request-scoped providers — they
outlive any single request. A per-request database session layered on an
app-scoped connection pool is the canonical pattern; see
:doc:`tutorial-sqlalchemy` for a complete example.

WebSocket handlers participate too: declare path parameters and
dependencies by name after the ``ws`` argument, and they're injected the
same way (a provider receives the socket via a ``ws``/``websocket`` (or
``req``/``request``) parameter or a ``WebSocket`` annotation)::

    @api.route("/ws/{room}", websocket=True)
    async def chat(ws, *, room, hub):
        await ws.accept()
        await hub.join(room, ws)

Handlers that only take ``ws`` keep working unchanged — names are injected
only when declared.


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

Both ``resp.file()`` and ``resp.stream_file()`` understand HTTP range
requests: clients sending ``Range: bytes=...`` receive ``206 Partial
Content`` with the requested slice. Multiple ranges are answered as
``multipart/byteranges``. This is what makes video seeking and resumable
downloads work — no extra code needed.

To prompt the browser to download rather than display, use
``resp.download()``, which streams the file (resumably) and sets
``Content-Disposition``::

    @api.route("/export")
    def export(req, resp):
        resp.download("reports/annual.pdf", filename="Annual Report.pdf")

.. warning::

   When the path comes from user input — a URL segment, a query parameter —
   pass ``root=`` to jail file access to a directory. ``resp.file``,
   ``resp.stream_file``, and ``resp.download`` resolve the path under
   ``root`` and return ``404`` on any ``../`` or symlink that escapes it::

       @api.route("/files/{name}")
       def serve(req, resp, *, name):
           resp.file(name, root="/srv/public")   # cannot escape /srv/public

Large *uploads* work the same way in reverse — iterate over the request
body in chunks instead of buffering it with ``await req.content``::

    @api.route("/upload", methods=["POST"])
    async def upload(req, resp):
        async with await anyio.open_file("incoming.bin", "wb") as f:
            async for chunk in req.stream():
                await f.write(chunk)

For ordinary multipart uploads, a typed ``File`` marker gives you Starlette's
``UploadFile`` plus a Responder convenience method for saving it::

    from responder import File, UploadFile

    @api.route("/avatar", methods=["POST"])
    async def avatar(req, resp, *, image: UploadFile = File(...)):
        path = await image.save("/srv/uploads/avatar.bin", create_parents=True)
        resp.media = {"saved": str(path)}

Since 9.0, multipart bodies parse *incrementally* off the wire: file parts
spool to temporary files (rolling to disk past ~1 MB) instead of being held
in memory, so a gigabyte upload does not mean a gigabyte of RAM — raise
``max_request_size`` to taste and ``UploadFile.save()`` moves the bytes
disk-to-disk in chunks.


Conditional Requests
--------------------

HTTP caching saves bandwidth and server time: clients remember a validator
for the responses they've seen, and the server answers ``304 Not Modified``
— no body — when nothing changed. Set ``resp.etag`` or
``resp.last_modified`` and Responder handles the comparison automatically::

    @api.route("/report")
    def report(req, resp):
        resp.etag = compute_version_hash()
        resp.text = expensive_render()   # skipped clients get a 304

When the request's ``If-None-Match`` header matches the ETag (or
``If-Modified-Since`` is at or after ``last_modified``), the client
receives ``304 Not Modified`` with empty body. ``resp.last_modified``
accepts a ``datetime`` or a preformatted HTTP-date string::

    from datetime import datetime, timezone

    @api.route("/feed")
    def feed(req, resp):
        resp.last_modified = datetime(2026, 1, 15, tzinfo=timezone.utc)
        resp.media = load_feed()

Per RFC 7232, ``If-None-Match`` takes precedence when both validators are
present, weak ETags (``W/"..."``) compare by their core value, and ``304``
handling applies only to ``GET`` and ``HEAD``.

The same validators also guard writes against lost updates. On
state-changing methods (anything but ``GET``/``HEAD``), when ``resp.etag``
or ``resp.last_modified`` is set, Responder evaluates the request's
``If-Match``, ``If-Unmodified-Since``, and ``If-None-Match`` preconditions
(RFC 9110 §13) and answers ``412 Precondition Failed`` — carrying the
current validator in the response headers — when they don't hold::

    @api.route("/items/{id}", methods=["PUT"])
    async def update_item(req, resp, *, id):
        item = load(id)
        resp.etag = item.version_hash    # the resource's current validator
        apply_changes(item, await req.media())
        resp.media = {"updated": True}

A client that sends ``If-Match: "<stale-tag>"`` receives a ``412`` instead
of the success response. Per RFC 9110, ``If-Match`` uses *strong*
comparison (a weak ``W/"..."`` ETag never matches it), ``If-Match: *``
succeeds whenever a validator is set, and ``If-Unmodified-Since`` is
consulted only when ``If-Match`` is absent. As with ``304`` handling, each
precondition is evaluated only when its validator is set (``resp.etag``
for the ETag preconditions, ``resp.last_modified`` for the date one), and
only for responses that would otherwise succeed (2xx) — a handler that
already set a ``404`` keeps its ``404``.

.. note::

   Preconditions are checked when the response is sent, *after* the
   handler has run. To skip the state change itself (not just the success
   response), compare the precondition in the handler before mutating —
   e.g. check ``req.headers.get("If-Match")`` against the current tag and
   return early with ``resp.status_code = 412``.

Don't want to manage validators yourself? Turn on automatic ETags and
every ``GET`` response gets a content-hash tag, with ``304`` handling for
free (note: the body is still rendered server-side; you save bandwidth,
not compute)::

    api = responder.API(auto_etag=True)

An explicitly set ``resp.etag`` always wins over the automatic one.

To control how long clients cache, set ``Cache-Control`` with the helper —
underscores become hyphens, ``True`` renders a bare directive::

    resp.cache_control(public=True, max_age=3600)
    # Cache-Control: public, max-age=3600

For common REST responses, the response object has small helpers that set the
status, headers, body, and media type together::

    @api.post("/items")
    def create_item(req, resp):
        item = save_item()
        resp.created({"id": item.id}, location=f"/items/{item.id}")

    @api.delete("/items/{id:int}")
    def delete_item(req, resp, *, id):
        delete_item_by_id(id)
        resp.no_content()

    @api.post("/items")
    def reject_duplicate(req, resp):
        resp.problem(409, "Item already exists", type="https://example.com/conflict")


After-Response Tasks
--------------------

Work that shouldn't delay the response — sending emails, recording
analytics, cache warming — can be deferred until after the client has the
bytes::

    @api.route("/signup", methods=["POST"])
    async def signup(req, resp):
        resp.media = {"ok": True}
        resp.background(send_welcome_email, "user@example.com")

Sync functions run in a thread pool; async functions run on the event
loop. Multiple tasks run in the order scheduled. (For fire-and-forget work
from anywhere — not tied to a response — use ``api.background`` instead.)


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

To raise an HTTP error from anywhere — a handler, a hook, a dependency —
call ``abort()`` instead of importing Starlette's exceptions::

    from responder import abort

    @api.route("/admin")
    def admin(req, resp):
        if not req.session.get("is_admin"):
            abort(403, detail="Forbidden")

``abort()`` halts the handler immediately and renders a content-negotiated
error — unlike setting ``resp.status_code = 403``, which doesn't stop
execution.

For richer errors, raise ``responder.Problem`` — a raisable RFC 9457
problem-details exception carrying ``type``, ``title``, ``instance``, and
arbitrary extension members into the rendered ``application/problem+json``
payload. Because it works from anywhere an exception propagates (views,
hooks, dependencies), it makes typed problem catalogs possible without
hand-building responses in every handler::

    from responder import Problem

    @api.route("/quota")
    def quota(req, resp):
        raise Problem(
            409,
            "You have used all 100 requests for today.",
            title="Quota Exceeded",
            type="https://api.example.com/errors/quota-exceeded",
            balance=0,
        )

The payload flows through the same machinery as framework errors —
``problem_handler`` enrichment, request IDs, and the app's JSON encoder —
and ``instance`` defaults to the request path per the RFC recommendation.
Passing any of these members to ``abort()`` raises a ``Problem`` for you:
``abort(409, type="...", balance=0)``.

Handlers can also be registered programmatically with
``api.add_exception_handler(exc_or_status, handler)`` (the
``@api.exception_handler`` decorator delegates to it). It accepts an
exception class *or* an integer status code, so you can catch a ``404``
directly. Registering against ``Exception`` (or ``500``) installs a
catch-all for unhandled server errors — though under ``debug=True`` the
traceback page is shown instead::

    async def not_found(req, resp, exc):
        resp.status_code = 404
        resp.media = {"error": "nothing here"}

    api.add_exception_handler(404, not_found)

Framework-generated errors use an RFC 9457-style envelope by default. Errors
such as 404, 405, validation failures, response-model validation failures, and
request timeouts use ``application/problem+json`` with ``type``, ``title``,
``status``, and ``detail`` fields. Validation errors also include ``errors``.

Pass ``problem_details=False`` when creating the app to keep the legacy
content-negotiated error format, where JSON clients receive bodies like
``{"error": "Not Found"}`` and browsers receive plain text.


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

When the hook belongs to just one endpoint, attach it directly to that route::

    def require_admin(req, resp):
        if not req.session.get("is_admin"):
            resp.status_code = 403
            resp.media = {"error": "forbidden"}

    @api.route("/admin", before=require_admin)
    def admin(req, resp):
        resp.media = {"ok": True}


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

The parentheses are optional — the bare ``@api.after_request`` works too,
as does the bare ``@api.before_request``.

Route-local after hooks use ``after=`` and run before global after hooks::

    def audit(req, resp):
        resp.headers["X-Audited"] = "1"

    @api.route("/reports", after=audit)
    def reports(req, resp):
        resp.media = []


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
by the browser, and doesn't require any special client-side libraries. A
typed SSE route validates and serializes every event, publishes its event
schema in OpenAPI, and generates a streaming client method::

    from collections.abc import AsyncIterator
    from pydantic import BaseModel

    class Progress(BaseModel):
        job_id: int
        percent: int

    @api.sse("/events", heartbeat=15)
    async def events(req, resp) -> AsyncIterator[responder.SSE[Progress]]:
        async for update in build_updates():
            yield responder.SSE(
                Progress(job_id=update.job_id, percent=update.percent),
                event="progress",
                id=update.sequence,
            )

On the client side, you consume SSE events with JavaScript's built-in
``EventSource`` API::

    const source = new EventSource("/events");
    source.onmessage = (event) => {
        console.log(event.data);
    };

Yield the annotated model directly when no SSE metadata is needed. Wrap it in
:class:`~responder.SSE` to set ``event``, ``id``, or ``retry``; comment-only
events are also supported::

    yield Progress(job_id=7, percent=42)
    yield responder.SSE(
        Progress(job_id=7, percent=42), event="update", id="1", retry=1500
    )
    yield responder.SSE(comment="still working")

For long-lived streams behind proxies, pass ``heartbeat=`` (seconds) to emit a
keepalive comment during idle periods so the connection isn't dropped. The
response also sets ``X-Accel-Buffering: no`` so events flush immediately::

    @api.sse("/events", heartbeat=15)
    async def events(req, resp) -> AsyncIterator[Progress]:
        ...

When the browser reconnects it sends the id of the last event it saw; read it
with :attr:`req.last_event_id <responder.Request.last_event_id>` to resume::

    @api.sse("/events", heartbeat=15)
    async def events(req, resp) -> AsyncIterator[Progress]:
        resume_from = req.last_event_id
        async for item in feed(after=resume_from):
            yield responder.SSE(item.payload, id=item.id)

Use :meth:`API.ndjson <responder.API.ndjson>` for the same typed, incremental
contract over newline-delimited JSON. Each yielded item becomes one compact
JSON line with media type ``application/x-ndjson``::

    @api.ndjson("/exports")
    async def exports(req, resp) -> AsyncIterator[Progress]:
        async for item in export_rows():
            yield item

The first immediately available item is validated before response headers are
sent, so a broken contract can still return ``500``. If a later item fails,
Responder logs the contract error and terminates the stream without writing
the invalid item. Producer cancellation closes the source iterator and keeps
request-scoped dependency teardown tied to the connection lifetime.

For untyped protocol frames and raw streaming escape hatches, the existing
``resp.sse()`` and ``resp.stream()`` APIs remain available.


GraphQL
-------

`GraphQL <https://graphql.org/>`_ is a query language for APIs that lets
clients request exactly the data they need — no more, no less. Instead of
multiple REST endpoints, you define a schema and let clients query it.

Responder includes built-in GraphQL support via
`Graphene <https://graphene-python.org/>`_. Install it with the
``graphql`` extra::

    $ uv pip install 'responder[graphql,orjson]'

Then set up a full GraphQL endpoint with a single method call::

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

For production, lock the endpoint down: turn off the in-browser IDE, reject
introspection queries, and cap query nesting depth to blunt
denial-of-service queries::

    api.graphql(
        "/graphql", schema=schema,
        graphiql=api.debug, introspection=api.debug, max_depth=10,
    )


.. _tour-openapi:

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

OpenAPI 3.1 is supported — pass ``openapi="3.1.0"``. The schema is served
as YAML by default; clients sending ``Accept: application/json`` get JSON,
and an ``openapi_route`` ending in ``.json`` (e.g. ``"/schema.json"``)
serves JSON always.

Path parameters are documented automatically from your route patterns:
``/pets/{id:int}`` produces a required integer path parameter in the spec,
with the OpenAPI-style template path (``/pets/{id}``).

**Every route appears automatically.** Responder builds the spec from each
route's methods, path parameters, body and response models, and any
``Query``/``Header``/``Cookie`` markers (see `Pydantic Validation`_) — so a
route shows up with its parameters and schemas even without a line of
annotation::

    @api.route("/health")
    def health(req, resp):
        resp.media = {"status": "ok"}     # documented as GET /health -> 200

Validating routes (anything with a request body or typed parameters) also
get an automatic ``422`` in the spec. To hide a route, pass
``include_in_schema=False``; the internal schema, docs, static, and metrics
endpoints are excluded for you::

    @api.route("/internal", include_in_schema=False)
    def internal(req, resp):
        resp.text = "private"

Operational responses are documented too. CSRF-protected unsafe routes show
``403``, capped request bodies show ``413``, rate-limited routes show
``429`` (and fail-closed backend ``503``), and configured request timeouts
show ``504``. With the default Problem Details contract, those responses
reference the reusable ``ProblemDetails`` schema.

Beyond that baseline, three tools let you enrich and override the generated
operations.

**Typed contracts** — the recommended approach. Add a required Pydantic-typed
handler parameter for the request body and a supported return annotation (or
an explicit ``response_model=``) for the response. Responder generates the
schema and validates at runtime: invalid bodies get a ``422`` with detailed
errors, and responses are serialized through the contract (extra fields
stripped, types enforced)::

    from pydantic import BaseModel

    class PetIn(BaseModel):
        name: str
        age: int = 0

    class PetOut(BaseModel):
        id: int
        name: str
        age: int

    @api.route("/pets", methods=["POST"])
    async def create_pet(req, resp, *, pet: PetIn) -> PetOut:
        return PetOut(id=1, name=pet.name, age=pet.age)

See `Pydantic Validation`_ for how these typed signatures behave at runtime.

**Decorator metadata** — for operation-level OpenAPI details that should stay
near the route, pass metadata directly to the route decorator. ``responses`` and
examples are deep-merged with generated response schemas and framework error
responses::

    @api.get(
        "/pets/{id:int}",
        response_model=PetOut,
        tags=["pets"],
        summary="Fetch a pet",
        responses={404: "Pet not found"},
        examples={
            "found": {
                "summary": "Existing pet",
                "value": {"id": 1, "name": "Fido", "age": 4},
            }
        },
        openapi_extra={"x-codeSamples": []},
    )
    def get_pet(req, resp, *, id):
        resp.media = {"id": id, "name": "Fido", "age": 4}

Routes whose success status isn't ``200`` can declare it with
``status_code=``. It does double duty: ``resp.status_code`` is pre-seeded
with it before the handler runs (so the handler only sets the body), and the
generated OpenAPI operation documents the success response under that status
instead of ``200``. Declaring ``status_code=204`` documents no response
body. An explicit ``resp.status_code = ...`` in the handler still wins::

    @api.route("/pets", methods=["POST"], status_code=201)
    async def create_pet(req, resp, *, pet: PetIn):
        resp.media = {"id": 1, "name": pet.name, "age": pet.age}   # -> 201

**YAML docstrings** — for fine-grained control, embed OpenAPI YAML in the
docstring; it is deep-merged *on top of* the auto-generated operation, so
you override only what you mention::

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

Before-request hooks registered on a group only run for paths under the
group's prefix — handy for guarding a whole API version with one check::

    @v1.before_request()
    def require_key(req, resp):
        if "X-Api-Key" not in req.headers:
            resp.status_code = 401
            resp.media = {"error": "missing API key"}

Groups register on a live ``api`` instance. To declare routes in separate
modules — without an ``API`` at all — use the standalone
:class:`responder.Router` and ``api.include_router()``; see :doc:`routers`.


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
        .with_app(path="/notebooks", root="./notebooks/dashboard.py")
        .with_app(path="/notebooks/analysis", root="./notebooks/analysis.py")
    )

    api.mount("", server.build())

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
        samesite="strict",   # never sent cross-site
        path="/",
    )

Cookies default to ``SameSite=Lax``, matching modern browser behavior and
defending against CSRF. Pass ``samesite="strict"`` for tighter isolation,
or ``samesite=None`` to omit the directive entirely.


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

Sessions are on by default (``sessions="auto"``). You read and write
``req.session`` like a dict; ``resp.session`` is a write-through view of the
same data, so ``resp.session = {"username": "alice"}`` replaces it wholesale.
Pass ``sessions=False`` to turn the middleware off entirely (then touching
``req.session`` raises a guiding ``RuntimeError``).

.. warning::

   **Set a stable secret key in production.** Responder signs cookie
   sessions with ``secret_key`` (or the ``RESPONDER_SECRET_KEY`` environment
   variable). If neither is set, it mints a *random per-process* key and
   logs a warning — fine for a quick demo, but it means every worker and
   every restart gets a different key, logging users out. Generate one once::

       python -c "import secrets; print(secrets.token_urlsafe(32))"

   then pass it explicitly (or via the env var)::

       api = responder.API(secret_key="<your-32+-char-random-key>")

   The old public placeholder ``secret_key="NOTASECRET"`` now raises
   ``SessionConfigError``, and ``sessions=True`` (strict mode) refuses to
   start without a real key.

Session cookies are ``HttpOnly`` always, and ``Secure`` in production
(``debug=False``) by default. Behind a TLS proxy this needs no action; pass
``session_https_only=False`` only when you genuinely serve plain HTTP, such
as local dev or tests over ``http://``. ``SameSite`` defaults to ``"lax"``;
``session_same_site="none"`` requires a Secure cookie. Adjust the cookie
lifetime with ``session_max_age`` (default 14 days).

By default, session data lives *in* the cookie (signed to prevent
tampering). That's simple but capped around 4KB and impossible to revoke
server-side. For logout-everywhere, large sessions, or sensitive data,
switch to server-side storage — only an opaque ID travels in the cookie (so
``secret_key`` no longer matters)::

    from responder.ext.sessions import MemorySessionBackend

    api = responder.API(session_backend=MemorySessionBackend())

The handler code (``req.session[...]``) is identical either way. For
multi-process deployments, use ``RedisSessionBackend(url=...)`` (or
``AsyncRedisSessionBackend`` in async apps) so all workers share the store.
Custom backends are duck-typed — any object with ``get(id)``,
``set(id, data, max_age)``, and ``delete(id)`` works. After a login or
privilege change, call ``regenerate_session(req)`` (from
``responder.ext.sessions``) to rotate the session ID and defeat session
fixation. For a full login flow, see :doc:`tutorial-auth`.

Treat session values as JSON-shaped data (dicts, lists, strings, numbers,
booleans, ``None``) so the same code works on every backend. The memory
backend stores Python objects as-is; the Redis backends serialize, and
their default codec also round-trips ``datetime``, ``date``, ``time``,
``Decimal``, ``UUID``, ``set``, ``frozenset``, and ``bytes`` values —
so ``req.session["expires"] = datetime.now()`` keeps working when you move
from the memory backend in development to Redis in production. Tuples come
back as lists on the Redis path (a JSON limitation). For anything richer,
pass ``serializer=`` to the Redis backends — any object with
``dumps(data)`` and ``loads(raw)`` methods works.



Static Files
------------

Most web applications serve static assets — CSS stylesheets, JavaScript
files, images, fonts. When a ``static/`` directory exists next to your app,
Responder serves it automatically — no configuration needed::

    api = responder.API()

Place your assets in the ``static/`` directory and they'll be served at
``/static/style.css``, ``/static/app.js``, etc. Use ``static_dir`` and
``static_route`` to serve a different directory or URL prefix.

The directory is never created for you: the implicit default ``static/``
is simply skipped if it doesn't exist, while a ``static_dir`` you pass
explicitly must exist — a missing one raises ``FileNotFoundError`` at
construction, so create the directory before pointing Responder at it.
Pass ``static_dir=None`` to disable static file serving entirely.

For single-page applications (React, Vue, Angular), you can serve
``index.html`` as the default response for all unmatched routes::

    import pathlib

    async def spa(req, resp):
        resp.html = pathlib.Path("static/index.html").read_text()

    api.add_route("/", spa, default=True)


CSRF Protection
---------------

Cookie sessions make browsers *ambient* authenticators: any site can make a
visitor's browser POST to your app, cookies included. `CSRF
<https://developer.mozilla.org/en-US/docs/Web/Security/Attacks/CSRF>`_
protection defeats that by requiring a token an attacker's page can't read.
It's off by default; switch it on and every unsafe request
(``POST``/``PUT``/``PATCH``/``DELETE``) must present the session's token or
it gets a ``403``::

    api = responder.API(csrf=True)

For HTML forms, pass the request into your template context and drop the
ready-made hidden input inside each ``<form method="post">``::

    {{ req.csrf_input }}

For JavaScript clients, read ``req.csrf_token`` (expose it in a page or a
small endpoint) and send it back in an ``X-CSRF-Token`` header. The token is
minted on first access, lives in the signed session, and stays stable for
the session's lifetime.

Endpoints that are *supposed* to receive cross-site POSTs — webhook
receivers, token-authenticated APIs — opt out per route; conversely a single
route can opt in without the app-wide switch::

    @api.route("/webhooks/stripe", methods=["POST"], csrf=False)
    async def stripe_webhook(req, resp): ...

    @api.route("/account/delete", methods=["POST"], csrf=True)
    async def delete_account(req, resp): ...

The token check shares the request's single (streaming) form parse, so a
protected multipart upload is still parsed exactly once.


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

This redirects HTTP requests to HTTPS and sends a
``Strict-Transport-Security`` header on responses. For a custom ``max-age``
or to enable preloading, install the middleware directly::

    from responder.middleware import HSTSMiddleware

    api.add_middleware(HSTSMiddleware, max_age=63072000, preload=True)


Security Headers
----------------

Opt in to common security headers on every response —
``X-Content-Type-Options: nosniff``, ``X-Frame-Options: DENY``, and
``Referrer-Policy: strict-origin-when-cross-origin``::

    api = responder.API(security_headers=True)

Add a Content-Security-Policy or Permissions-Policy, or override any header,
by passing options through::

    api = responder.API(security_headers={
        "content_security_policy": "default-src 'self'",
        "headers": {"X-Frame-Options": "SAMEORIGIN"},
    })

A header a handler sets itself is always left untouched.


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


Authentication
--------------

``responder.ext.auth`` provides Bearer, Basic, and API-key schemes. Each one
is a callable that pulls the credential from the request, verifies it, and
returns the principal your ``verify`` callback produced — or raises ``401``
with the right ``WWW-Authenticate`` challenge. The most direct form is
``auth=`` on a route::

    from responder.ext.auth import BearerAuth

    auth = BearerAuth(verify=lambda token: users.get(token))

    @api.get("/me", auth=auth)
    async def me(req, resp, *, user):
        resp.media = {"user": user}

Responder enforces the scheme, registers the OpenAPI security scheme when
OpenAPI is enabled, stores the principal on ``req.state.user`` /
``req.state.auth``, and injects it into ``user``, ``principal``, or ``auth``
parameters. The older explicit form still works when you want to separate
runtime dependency wiring from documentation::

    auth.register(api)
    api.add_dependency("user", auth)

    @api.get("/me", security=["bearerAuth"])
    async def me(req, resp, *, user):
        resp.media = {"user": user}

``verify`` may be sync or async; return a truthy principal to accept or a falsy
value to reject. For static secrets, pass them directly and the scheme compares
them in constant time::

    BearerAuth(tokens=["s3cret"])
    APIKeyAuth(keys=["abc123"], name="X-API-Key")          # or location="query"
    BasicAuth(credentials={"alice": "password"})

Calling ``register()`` (or :meth:`api.add_security_scheme`) makes the scheme
appear in the OpenAPI document, so the interactive docs grow an **Authorize**
button. ``security=`` on a route marks which operations require it; pass
``default=True`` to ``add_security_scheme`` to require a scheme everywhere.


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

An inbound ID is honored only when it looks like a request ID — at most 128
characters of letters, digits, ``.``, ``_``, and ``-``. Anything else (an
oversized header, control characters) is discarded and a fresh UUID minted
instead, so a client can't bloat your responses or log lines.
``enable_logging=True`` applies exactly the same policy and mints the same
UUID format, so switching access logging on or off never changes the IDs
your log pipeline parses.


Request Size Limits
-------------------

Unbounded request bodies are an easy denial-of-service vector, so since 9.0
bodies are capped at **100 MiB by default**. Oversized requests get ``413``
automatically — whether the body is read with ``await req.content``,
``req.media()``, or streamed. Tune the cap (or disable it) per application::

    api = responder.API(max_request_size=10 * 1024 * 1024)  # 10 MB
    api = responder.API(max_request_size=None)              # unlimited (pre-9.0)

The check fails fast on the ``Content-Length`` header when present, and
enforces the limit cumulatively for chunked uploads. Multipart uploads
stream to disk as they arrive, so a generous cap does not translate into
memory pressure.


Request Timeouts
----------------

A handler stuck on a slow database or unresponsive upstream shouldn't hold
the client forever. Set an application-wide budget and overruns are
answered with ``504 Gateway Timeout``::

    api = responder.API(request_timeout=30)  # seconds

Dependency teardowns still run when a request times out. One caveat: a
*synchronous* handler running in the thread pool can't be interrupted —
the client gets the 504 on time, but the thread runs to completion in the
background.

WebSocket handlers have a matching guard. Pass ``ws_idle_timeout`` to close a
connection that waits too long for the next inbound message (close code
``1001``)::

    api = responder.API(ws_idle_timeout=30)  # seconds

The deadline resets on every message received, so it bounds *idle* time between
messages rather than the total connection lifetime — a continuously active
client is never closed. It defaults to ``None`` (no timeout).


.. _tour-rate-limiting:

Rate Limiting
-------------

Rate limiting prevents individual clients from overwhelming your API with
too many requests. It's essential for public APIs, and good practice even
for internal ones.

Responder includes a built-in rate limiter (a sliding window in memory; a
fixed window on the Redis backends)::

    from responder.ext.ratelimit import RateLimiter

    limiter = RateLimiter(requests=100, period=60)  # 100 req/min
    limiter.install(api)

When the limit is exceeded, clients receive a ``429 Too Many Requests``
response with a ``Retry-After`` header. Every response includes
``X-RateLimit-Limit``, ``X-RateLimit-Remaining``, and ``X-RateLimit-Reset``
(seconds until the window resets) headers so clients can pace themselves.
With the default ``problem_details=True`` setting, over-limit and fail-closed
backend responses use the same ``application/problem+json`` envelope as
framework-generated errors; pass ``problem_details=False`` on the API to keep
the legacy ``{"error": ...}`` JSON body.

The rate limiter is per-client, keyed by IP address by default. To key by
something else — an API key, an authenticated user id — pass ``key=``, a
callable receiving the request::

    limiter = RateLimiter(
        requests=100, period=60,
        key=lambda req: req.headers.get("x-api-key", "anonymous"),
    )

If the key function returns ``None`` (or an empty string), the request
falls back to the IP-based key.

By default, counts live in process memory. The in-memory store tracks at most
``max_keys`` distinct clients (100k by default), evicting the least-recently
seen beyond that, so a client rotating source IPs can't grow memory without
bound. For multi-process or multi-host deployments, plug in the Redis backend so
all workers share one budget::

    from responder.ext.ratelimit import RateLimiter, RedisBackend

    limiter = RateLimiter(
        requests=100, period=60,
        backend=RedisBackend(url="redis://localhost:6379/0"),
    )

In async apps, reach for ``AsyncRedisBackend`` instead — it talks to Redis
without a thread-pool hop. It's async-only, so drive it with
``limiter.install(api)`` (or ``await limiter.acheck(req, resp)``) rather than
a sync route::

    from responder.ext.ratelimit import RateLimiter, AsyncRedisBackend

    limiter = RateLimiter(
        requests=100, period=60,
        backend=AsyncRedisBackend(url="redis://localhost:6379/0"),
    )
    limiter.install(api)

A word on outages: when the backend errors out (say, Redis is unreachable),
the limiter answers ``503 Service Unavailable`` by default — nothing slips
past the limit, but rate-limited routes go down with the backend. Pass
``fail_open=True`` to make the opposite trade-off: requests are let through
unmetered, with a warning logged, until the backend recovers. Choose
``fail_open=True`` when availability matters more than strict enforcement
(most public APIs); keep the default when the limiter is a security control
(login attempts, expensive mutations).

Any object with a ``hit(key, max_requests, period)`` method returning
``(allowed, remaining)`` — or ``(allowed, remaining, reset_after)`` to
populate ``X-RateLimit-Reset`` — works as a backend (``ahit`` for the async
variant), so custom stores are easy to write.

To rate-limit a single route instead of the whole API, apply
:meth:`~responder.ext.ratelimit.RateLimiter.limit` beneath ``@api.route``.
Give each route its own ``RateLimiter`` so budgets stay independent::

    expensive_limiter = RateLimiter(requests=5, period=60)

    @api.route("/reports")
    @expensive_limiter.limit
    async def generate_report(req, resp):
        ...

.. note::

   Prefer ``limiter.install(api)`` or ``@limiter.limit`` over calling
   ``limiter.check(req, resp)`` from a hand-written before-request hook.
   All three enforce identically, but only the first two are visible to the
   OpenAPI generator — a manual ``check()`` call is arbitrary code, so the
   generated schema won't list the ``429``/``503`` responses for it.


Metrics
-------

Production services need visibility into traffic and latency. Responder
ships a zero-dependency metrics endpoint in Prometheus text format::

    api = responder.API(metrics_route="/metrics")

Every request is recorded as a counter
(``responder_requests_total{method,path,status}``), a latency histogram
(``responder_request_duration_seconds``), and an in-flight gauge
(``responder_requests_in_flight`` — requests currently being handled, the
series capacity dashboards want). Labels use the route *pattern*
(``/users/{id}``), not the raw path, so cardinality stays bounded; requests
matching no route are labelled ``unmatched``. Point Prometheus, Grafana
Alloy, or any compatible scraper at the endpoint and you have dashboards.

The histogram's bucket bounds default to 5ms–10s
(:data:`responder.ext.metrics.BUCKETS`). If your latency profile is
different — 30-second report endpoints, sub-millisecond cache hits — pass
your own ascending bounds via ``metrics_buckets=`` (or construct a
:class:`~responder.ext.metrics.MetricsCollector` with ``buckets=``
directly)::

    api = responder.API(
        metrics_route="/metrics",
        metrics_buckets=(0.1, 0.5, 1.0, 5.0, 30.0),
    )


Health Checks
-------------

Orchestrators (Kubernetes, load balancers) want a readiness endpoint that
reflects whether the app's dependencies are actually reachable. Register
checks and Responder aggregates them::

    api = responder.API(health_route="/health")

    api.add_health_check("db", lambda: database.ping())
    api.add_health_check("cache", check_redis)

A check passes unless it returns ``False`` or raises. The endpoint returns
``200`` with ``{"status": "ok", "checks": {...}}`` when all pass, and ``503``
otherwise — with each check's status (and the error detail for any that
raised). Checks may be sync or async, and the route is excluded from the
OpenAPI schema. (``add_health_check`` adds the route at ``/health`` on first
use if you didn't set ``health_route``.)


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

For debugging route control flow, pass ``trace_dispatch=True``. Responder emits
debug logs for the documented dispatch stages: before hooks, auth,
dependencies, handler execution, and after hooks::

    api = responder.API(enable_logging=True, trace_dispatch=True)


Pydantic Validation
-------------------

`Pydantic <https://docs.pydantic.dev/>`_ models integrate directly with
Responder's routing. Add a required Pydantic-typed body parameter to validate
incoming data and annotate the return value to control the outgoing shape::

    from pydantic import BaseModel

    class ItemIn(BaseModel):
        name: str
        price: float

    class ItemOut(BaseModel):
        id: int
        name: str
        price: float

    @api.route("/items", methods=["POST"])
    async def create_item(req, resp, *, item: ItemIn) -> ItemOut:
        return ItemOut(id=1, **item.model_dump())

When a write-method handler has a required Pydantic-typed body parameter:

- Valid requests are parsed, validated, and injected as a model instance.
- Invalid requests get an automatic ``422 Unprocessable Entity`` response with
  detailed error messages — you don't write any validation code.

When a response contract is inferred or explicitly set:

- The response is serialized through the model before being sent
- Extra fields are stripped automatically
- Type coercion happens at the boundary

For individual query parameters, headers, and cookies, declare them as
keyword-only arguments with a *marker* default. Responder reads the value,
coerces it to the annotated type, and injects it — returning ``422`` if a
required value is missing or won't coerce::

    from responder import Query, Header

    @api.route("/search")
    def search(req, resp, *,
               q: str = Query(...),
               limit: int = Query(10),
               tags: list[str] = Query([]),
               user_agent: str = Header("unknown")):
        resp.media = {"q": q, "limit": limit, "tags": tags}

- ``Query(...)`` (an Ellipsis) marks the value **required**; ``Query(10)``
  makes it optional with that default.
- The annotation drives coercion: ``"5"`` becomes ``5``, and a sequence
  type like ``list[str]`` collects repeated keys (``?tags=a&tags=b``).
- ``Header`` looks up the header named after the parameter, converting
  underscores to dashes (``user_agent`` → ``user-agent``); pass ``alias=``
  for an explicit name. ``Cookie`` reads cookies, and ``Path`` re-validates
  or renames a path segment::

      from responder import Path

      @api.route("/users/{uid:int}")
      def get_user(req, resp, *, user_id: int = Path(..., alias="uid")):
          resp.media = {"id": user_id}

All four markers are exported from the top level
(``from responder import Query, Header, Cookie, Path``) and feed the
generated OpenAPI ``parameters``.

To validate the whole query string as a single model instead, use
``params_model`` — values are coerced, defaults apply, repeated keys map to
``list`` fields, invalid queries get a ``422``, and the parameters appear in
your OpenAPI spec::

    class SearchParams(BaseModel):
        q: str
        limit: int = 10

    @api.route("/search", params_model=SearchParams)
    async def search(req, resp):
        params = req.state.validated_params
        resp.media = await find(params.q, limit=params.limit)

The body and the response validate from type hints too. On
``POST``/``PUT``/``PATCH``/``DELETE``, a keyword-only parameter annotated
with a Pydantic model (and no default) receives the parsed, validated body —
and a supported return annotation becomes the response contract::

    @api.route("/items", methods=["POST"])
    async def create_item(req, resp, *, item: ItemIn) -> ItemOut:
        return ItemOut(id=1, name=item.name, price=item.price)

An invalid or non-object body returns ``422`` before your handler runs. The
``-> ItemOut`` return annotation validates and coerces the outgoing value and
strips undeclared fields; if the payload violates the contract it fails closed
(a ``500`` in production, or re-raises under ``debug=True``) rather than
leaking a malformed response. Validation runs after after-request hooks, so the
contract describes what is actually sent. Opt out with
``@api.route(..., response_model=False)``.

Inference uses Pydantic's ``TypeAdapter`` support boundary. Models,
dataclasses, typed dictionaries, ``list[ItemOut]``, ``Page[ItemOut]``, unions,
and JSON scalar types all drive runtime validation, OpenAPI, and generated
clients. A union like ``ItemOut | ErrorOut`` emits ``anyOf``/``oneOf`` and
validates the selected branch. String and bytes annotations default to
``text/plain`` and ``application/octet-stream``; other inferred contracts use
``application/json``. Explicit ``response_model=`` remains available when the
signature cannot or should not carry the contract.

.. note::

   A raw ORM object needs a model configured with ``from_attributes=True``, or
   an explicit ``ItemOut.model_validate(obj)`` before it is returned.

This is the recommended way to build validated REST APIs with Responder.
See the :doc:`tutorial-rest` for a complete walkthrough.


Pagination
----------

``responder.ext.pagination`` provides a generic ``Page`` envelope and a
``paginate`` helper for list endpoints. Pair them with the typed ``Query``
markers and a ``Page[Model]`` response model::

    from responder import Query
    from responder.ext.pagination import Page, paginate

    @api.get("/items")
    def list_items(req, resp, *,
                   page: int = Query(1, ge=1),
                   size: int = Query(20, ge=1, le=100)) -> Page[Item]:
        return paginate(db.all(), page=page, size=size)

The response is an envelope with ``items``, ``total``, ``page``, ``size``, and
``pages``, and OpenAPI documents it as an inline object referencing your element
model. ``paginate`` slices an in-memory collection by default; when you page in
the database yourself, pass the already-sliced rows plus the overall
``total=``::

    rows = db.query(limit=size, offset=(page - 1) * size)
    resp.media = paginate(rows, page=page, size=size, total=db.count())

Many REST consumers navigate by response *headers* instead of the body
envelope — the GitHub-style :rfc:`8288` ``Link`` header plus
``X-Total-Count``. Call ``set_pagination_headers`` to emit both::

    from responder.ext.pagination import paginate, set_pagination_headers

    @api.get("/items")
    def list_items(req, resp, *,
                   page: int = Query(1, ge=1),
                   size: int = Query(20, ge=1, le=100)) -> Page[Item]:
        result = paginate(db.all(), page=page, size=size)
        set_pagination_headers(req, resp, result)
        return result

The ``Link`` header carries ``rel="first"``/``"prev"``/``"next"``/``"last"``
URLs built from the request's own URL — every other query parameter
(filters, sort specs) is preserved; only ``page`` and ``size`` are
rewritten. ``prev`` is omitted on the first page and ``next`` on the last.


Sorting and Filtering
~~~~~~~~~~~~~~~~~~~~~~~

``responder.ext.query`` rounds out list endpoints with ``sort_items`` and
``filter_items`` — in-memory helpers (dicts or objects, no ORM coupling) that
pair with the typed markers and ``paginate``::

    from responder.ext.query import filter_items, sort_items

    @api.get("/items")
    def list_items(req, resp, *,
                   status: str = Query(None),
                   sort: str = Query("name"),
                   page: int = Query(1, ge=1),
                   size: int = Query(20, ge=1, le=100)) -> Page[Item]:
        rows = filter_items(db.all(), {"status": status})
        rows = sort_items(rows, sort, allowed={"name", "created_at"})
        return paginate(rows, page=page, size=size)

``filter_items`` applies ``field == value`` equality and skips entries whose
value is ``None`` (so optional markers pass straight through). ``sort_items``
reads a ``name,-created`` spec (``-`` = descending, multiple keys allowed);
always pass ``allowed=`` for a client-supplied ``sort`` so users can't order by
arbitrary attributes — an out-of-list field returns ``400``.


.. _tour-content-negotiation:

Content Negotiation
-------------------

Responder automatically negotiates the response format based on the
client's ``Accept`` header. Set ``resp.media`` to a Python object and
the right thing happens:

- ``Accept: application/json`` (default) → JSON
- ``Accept: application/yaml`` → YAML (the legacy ``application/x-yaml``
  is accepted too)
- ``Accept: application/x-msgpack`` → MessagePack

This means a single endpoint serves multiple formats without any
conditional logic in your code::

    @api.route("/data")
    def data(req, resp):
        resp.media = {"key": "value"}

Clients get the format they ask for::

    $ curl http://localhost:5042/data
    {"key": "value"}

    $ curl -H "Accept: application/yaml" http://localhost:5042/data
    key: value

When the ``Accept`` header ranks several supported formats, Responder
serves the one the client prefers — the highest q-value wins, per
RFC 9110 §12.5.1 — rather than the first format registered::

    $ curl -H "Accept: application/yaml;q=0.9, application/json;q=0.1" \
        http://localhost:5042/data
    key: value

Without an ``Accept`` header, or when everything ties (e.g. ``*/*``),
JSON remains the default. To apply the same ranking yourself — say, to
choose between representations you render by hand — use
``req.preferred_media_type()``, which returns the candidate the client
prefers (ties keep your order) or ``None`` when it accepts none of them::

    @api.route("/export")
    def export(req, resp):
        preferred = req.preferred_media_type(["application/json", "text/csv"])
        if preferred == "text/csv":
            resp.text = render_csv()
            resp.mimetype = "text/csv"
        else:
            resp.media = render_dict()


Faster JSON with orjson
-----------------------

The recommended Responder installation includes
`orjson <https://github.com/ijl/orjson>`_::

    $ pip install "responder[orjson]"

Responder detects it automatically and uses it to encode JSON responses —
typically 3-10x faster than the standard library, which matters for large
payloads that would otherwise serialize on the event loop. No code changes
are needed: ``resp.media`` and custom ``API(encoder=...)`` hooks keep working
exactly as before. Request decoding remains on the standard library to
preserve arbitrary-precision integers and compatibility with ``NaN`` and
``Infinity``. The legacy ``API(json_ensure_ascii=True)`` mode also stays on
the standard library, since orjson emits UTF-8 only.

Free-threaded CPython builds (``3.14t``/``3.15t``) can install the base
``responder`` package. If orjson is unavailable, Responder transparently
falls back to the standard-library encoder.

Output is byte-identical to the stdlib encoder except for whitespace
(orjson emits compact ``,``/``:`` separators) and one corner: float
``nan``/``inf`` values serialize as ``null`` instead of the non-standard
``NaN``/``Infinity`` literals. Payloads orjson cannot handle (such as
integers beyond 64 bits) automatically fall back to the standard library,
so nothing that serialized before stops working.


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
