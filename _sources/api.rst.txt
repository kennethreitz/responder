API Reference
=============

This page documents Responder's public Python API. For usage examples
and explanations, see the :doc:`quickstart` and :doc:`tour`.


The API Class
-------------

The central object of every Responder application. It holds your routes,
middleware, templates, and configuration. Create one at the top of your
module and use it to define your entire web service.

Quick example::

    import responder

    api = responder.API(
        title="My Service",           # OpenAPI title
        version="1.0",                # OpenAPI version
        openapi="3.0.2",              # enable OpenAPI
        docs_route="/docs",           # Swagger UI at /docs
        cors=True,                    # enable CORS
        allowed_hosts=["example.com"],
    )

.. note::

   Cookie sessions are on by default (``sessions="auto"``). With no key set,
   Responder mints a random per-process signing key at startup and logs a
   warning — fine for a single dev process, but multi-worker or multi-instance
   deploys need a *stable* key so signed cookies validate everywhere. Set one
   with ``secret_key=`` or the ``RESPONDER_SECRET_KEY`` environment variable::

       python -c "import secrets; print(secrets.token_urlsafe(32))"

   ``secret_key="NOTASECRET"`` (the old public default) now raises
   :class:`~responder.ext.sessions.SessionConfigError`, and ``sessions=True``
   with no key raises as well. Pass ``sessions=False`` for a stateless service.
   See :doc:`guide-config` for the full secret-key and session-cookie story.

.. module:: responder

.. autoclass:: API
    :inherited-members:


Request
-------

The request object is passed into every view as the first argument. It
gives you access to everything the client sent — headers, query
parameters, the request body, cookies, and more.

Most properties are synchronous, but reading the body requires ``await``
because it involves I/O.

Common patterns::

    # The request method — UPPERCASE: "GET", "POST", ...
    if req.method == "POST":
        ...

    # Headers (case-insensitive)
    token = req.headers.get("Authorization")

    # Every raw line of a repeated header, in order
    hops = req.headers.get_list("X-Forwarded-For")

    # Query parameters: /search?q=python&page=2
    query = req.params["q"]

    # JSON body (async handlers)
    data = await req.media()

    # ...or synchronously, from a sync handler
    data = req.media_sync()
    body = req.text_sync

    # Form data and file uploads
    form = await req.media("form")
    files = await req.media("files")

    # Client info
    ip, port = req.client
    is_https = req.is_secure

.. note::

   ``req.method`` is a plain UPPERCASE ``str`` (``"GET"``, ``"POST"``).
   Comparisons are case-sensitive, so compare against uppercase literals:
   ``req.method == "GET"``.

   ``await req.media("files")`` returns ``{name: UploadFile}`` (streamed,
   spooled to disk). See `Parameter Markers`_ for the typed ``File()`` form.

For reading typed query parameters, headers, and cookies straight off the
signature, see `Parameter Markers`_ below.

.. autoclass:: Request
    :inherited-members:


Response
--------

The response object is passed into every view as the second argument.
Mutate it to control what gets sent back to the client — the body,
status code, headers, and cookies.

Common patterns::

    resp.text = "plain text"            # text/plain
    resp.html = "<h1>Hello</h1>"        # text/html
    resp.media = {"key": "value"}       # application/json
    resp.content = b"raw bytes"         # application/octet-stream

    # Serve files. Pass root= to jail a user-supplied path under a directory —
    # a "../" or symlink escape returns 404 instead of leaking the filesystem:
    resp.file("reports/q3.pdf", root="exports")       # auto content-type
    resp.stream_file("exports/big.csv", root="exports")  # streamed

    resp.status_code = 201
    resp.headers["X-Custom"] = "value"
    resp.cookies["session"] = "abc123"

    # Common response helpers:
    resp.created({"id": 1}, location="/items/1")  # 201 + Location
    resp.no_content()                            # 204 + empty body
    resp.problem(409, "Already exists")          # application/problem+json

    # Redirect (external targets allowed by default; pass
    # allow_external=False to refuse off-site URLs):
    resp.redirect("/dashboard")

.. note::

   Handlers can also *return* the body Flask-style instead of mutating
   ``resp``: ``return body``, ``return body, status``, or
   ``return body, status, headers``. Pydantic models and dataclasses serialize
   natively. A supported return annotation validates the body and supplies its
   OpenAPI/generated-client type; use ``response_model=False`` to opt out.
   Status-specific contracts use ``responses={404: ErrorModel}``, or a mapping
   with ``model`` plus additional OpenAPI metadata.
   ``resp.session`` is a read/write view of ``req.session`` and raises
   ``RuntimeError`` when the app is built with ``sessions=False``.

.. autoclass:: Response
    :inherited-members:


Typed Streams
-------------

Use :meth:`API.sse` and :meth:`API.ndjson` for item-by-item validation,
serialization, OpenAPI schemas, and generated streaming clients. ``SSE`` is
the optional metadata envelope for a typed Server-Sent Event.

.. autoclass:: SSE
    :members:


Parameter Markers
-----------------

Inject validated query parameters, headers, cookies, and path parameters
straight into a handler's signature. A marker goes in the *default* slot of a
keyword-only argument — it is not a decorator::

    from responder import Query, Header

    @api.route("/search")
    def search(req, resp, *,
               q: str = Query(...),            # required
               limit: int = Query(10),         # optional, defaults to 10
               tags: list[str] = Query(...),   # repeated keys: ?tags=a&tags=b
               token: str = Header(None, alias="X-Token")):
        resp.media = {"q": q, "limit": limit, "tags": tags}

``Query(...)`` (an Ellipsis) marks a required parameter; ``Query(value)``
supplies a default. Each value is coerced to the parameter's type annotation
with Pydantic; a missing required value or a coercion failure returns
``422 Unprocessable Entity`` with a body of ``{"errors": [...]}`` aggregating
every failing parameter.

- :func:`~responder.Query` reads the query string. A ``list`` / ``list[int]``
  annotation collects repeated keys.
- :func:`~responder.Header` reads request headers; the parameter name is
  matched with underscores converted to dashes (``user_agent`` →
  ``user-agent``) unless you pass ``alias=``.
- :func:`~responder.Cookie` reads cookies by name (no underscore conversion).
- :func:`~responder.Path` re-validates or renames a path parameter
  (``Path(..., alias="uid")``). A path parameter always wins over a same-named
  query/header/cookie marker.

Markers also accept Pydantic field constraints, which are enforced at runtime
(returning ``422`` on violation) and emitted into the schema, along with
``description=`` and ``deprecated=``::

    @api.route("/search")
    def search(req, resp, *,
               q: str = Query(..., min_length=3, description="search term"),
               limit: int = Query(10, ge=1, le=100)):
        ...

An unknown keyword (a typo such as ``Query(dafault=5)``) raises immediately.

Markers may also be written in :pep:`593` ``Annotated`` form, which keeps the
parameter's default value in the usual slot::

    from typing import Annotated

    def search(req, resp, *, q: Annotated[str, Query(min_length=3)] = "all"):
        ...

Markers also drive the generated OpenAPI ``parameters`` and add an automatic
``422`` to validating routes. For full request/response validation with
Pydantic models, see :doc:`tutorial-rest`.

File uploads and form fields
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:func:`~responder.File` injects an uploaded file as an
:class:`~responder.UploadFile` (read it with ``await f.read()``, or stream it
in chunks — large uploads are spooled to disk, not held in memory).
:func:`~responder.Form` injects a form field (urlencoded or multipart),
coerced and validated like :func:`~responder.Query`::

    from responder import File, Form, UploadFile

    @api.route("/upload", methods=["POST"])
    async def upload(req, resp, *,
                     document: UploadFile = File(...),
                     title: str = Form(...),
                     tags: list[str] = Form([])):
        saved = await document.save("/srv/uploads/document.bin")
        resp.media = {"name": document.filename, "path": str(saved)}

A sequence annotation (``list[UploadFile]``) collects multiple files sent under
one field name. These routes generate a ``multipart/form-data`` (or
``application/x-www-form-urlencoded``) request body in OpenAPI, so the
interactive docs show a file picker. ``await req.media("files")`` returns the
same ``UploadFile`` objects keyed by field name.

``UploadFile.save(path)`` streams the upload to disk and returns the resulting
``Path``. Pass ``create_parents=True`` to create the parent directory first, or
``seek_start=False`` if you intentionally want to save from the file's current
read position.

Binding a whole form to a Pydantic model
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Instead of one marker per field, a parameter annotated with a Pydantic model
and a ``Form(...)`` default binds the *entire* parsed form (urlencoded or
multipart) in one shot — fields are coerced by the model, model defaults
apply, and a validation failure returns the same ``422`` payload as JSON body
models, with each error located at ``["form", <field>]``::

    from pydantic import BaseModel
    from responder import Form

    class ProfileForm(BaseModel):
        name: str
        age: int = 0
        tags: list[str] = []          # repeated form keys collect into lists

    @api.route("/profiles", methods=["POST"])
    async def create(req, resp, *, profile: ProfileForm = Form(...)):
        resp.media = profile.model_dump()

Uploaded files bind to model fields declared as ``UploadFile`` — Pydantic
requires ``arbitrary_types_allowed`` for that::

    from pydantic import ConfigDict
    from responder import UploadFile

    class AvatarForm(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        name: str
        avatar: UploadFile

Field aliases are honored (the form key is the alias), and ``Form(None)``
makes the whole model optional: an empty form yields the default instead of a
``422``. Markers — including form models — work identically on class-based
view methods, and ``Query``/``Header``/``Cookie``/``Path`` markers also
resolve on WebSocket handlers from the handshake request (a validation
failure there closes the socket with code ``1008``).

Explicit dependencies
~~~~~~~~~~~~~~~~~~~~~

Use :func:`~responder.Depends` when one route needs a local provider and you
don't want to register it app-wide with ``api.dependency``::

    from responder import Depends

    def current_user(req):
        return decode_user(req.headers.get("Authorization"))

    @api.route("/me")
    def me(req, resp, *, user=Depends(current_user)):
        resp.media = {"user": user}

The provider follows the same rules as registered dependencies: it may be sync,
async, a generator, or an async generator, and it may receive the current
request or registered dependencies.

Use ``dependencies=[Depends(...)]`` when a dependency is a guard or setup step
and the handler does not need its return value. It participates in
dependency registration/caching, sub-dependency resolution, and generator
teardown::

    def require_user(req):
        if "Authorization" not in req.headers:
            responder.abort(401, detail="Not authenticated")

    @api.route("/private", dependencies=[Depends(require_user)])
    def private(req, resp):
        resp.media = {"ok": True}

Use ``before=`` / ``after=`` for raw route hooks: imperative code that should
run around the handler without entering the dependency graph. Hooks may
short-circuit by setting ``resp.status_code`` and are best for request/response
mutation or simple guards that do not need dependency caching, sub-dependencies,
OpenAPI security integration, or generator teardown::

    def require_json(req, resp):
        if not req.is_json:
            resp.status_code = 415
            resp.media = {"error": "JSON required"}

    @api.route("/events", before=require_json)
    def events(req, resp):
        resp.media = req.headers

The three paths are intentionally distinct:

- Handler parameters with ``Depends(...)`` resolve a value and pass it into the
  handler.
- Route ``dependencies=[Depends(...)]`` resolve graph-aware providers for their
  side effects and ignore the return value.
- Route ``before=`` / ``after=`` hooks are raw hooks; they are not dependency
  providers.

Execution order for a route is:

1. Global ``before_request`` hooks
2. Per-route ``before`` hooks
3. Per-route auth helpers
4. Route validation/input parsing
5. Side-effect ``dependencies``
6. Handler view(s)
7. Response-model validation
8. Per-route and global ``after`` hooks

.. autofunction:: responder.Query

.. autofunction:: responder.Header

.. autofunction:: responder.Cookie

.. autofunction:: responder.Path

.. autofunction:: responder.Form

.. autofunction:: responder.File

.. autofunction:: responder.Depends


Route Groups
------------

Group related routes under a shared URL prefix — useful for API versioning
and organizing large applications::

    v1 = api.group("/v1")

    @v1.route("/users")
    def list_users(req, resp):
        resp.media = []

.. autoclass:: responder.api.RouteGroup
    :members:


Standalone Routers
------------------

:class:`responder.Router` records route declarations without an ``API``
instance, so routes can live in separate modules (like Flask blueprints)
and be attached later — with prefix composition, nesting, and group-level
``tags`` / ``dependencies`` / ``auth``. See :doc:`routers` for the guide. ::

    from responder import Router

    router = Router(prefix="/users", tags=["users"])

    @router.get("/{user_id:int}")
    def get_user(req, resp, *, user_id):
        resp.media = {"id": user_id}

    api.include_router(router, prefix="/v1")

.. autoclass:: responder.Router
    :members:


Route-Local Hooks and Auth
--------------------------

Use global ``before_request`` / ``after_request`` hooks for behavior that
applies to the whole app. For behavior that belongs to one endpoint, pass
``before=`` and ``after=`` to the route decorator::

    def require_json(req, resp):
        if not req.is_json:
            resp.status_code = 415
            resp.media = {"error": "JSON required"}

    def add_audit_header(req, resp):
        resp.headers["X-Audited"] = "1"

    @api.post("/events", before=require_json, after=add_audit_header)
    async def events(req, resp):
        resp.media = await req.media()

Authentication helpers from :mod:`responder.ext.auth` can be attached directly
with ``auth=``. Responder enforces the scheme, registers OpenAPI security when
OpenAPI is enabled, stores the principal on ``req.state.user`` /
``req.state.auth``, and injects it into a ``user``, ``principal``, or ``auth``
handler parameter::

    from responder.ext.auth import BearerAuth

    bearer = BearerAuth(tokens=["s3cret"])

    @api.get("/me", auth=bearer)
    def me(req, resp, *, user):
        resp.media = {"user": user}

Use ``API(auth=bearer)`` when most routes share the same auth scheme. Routes
inherit the app auth by default; pass ``auth=None`` on public routes such as
``/login`` or ``/health``.

Use ``api.policy(name, auth)`` to give a reusable auth requirement an
application-facing name while keeping the wrapped scheme's runtime and OpenAPI
behavior unchanged::

    admin = api.policy("admin", bearer.requires("admin"))

    @api.get("/admin", auth=admin)
    def admin_dashboard(req, resp, *, user):
        resp.media = {"user": user}

Use ``auth.optional()`` when credentials should be accepted but not required.
Missing credentials inject ``None`` into ``user`` / ``principal`` / ``auth``;
invalid credentials still return ``401``. OpenAPI documents both anonymous and
authenticated access::

    optional = bearer.optional()

    @api.get("/maybe", auth=optional)
    def maybe(req, resp, *, user):
        resp.media = {"user": user}

Use ``auth.requires(...)`` or ``ScopedAuth`` when a route needs lightweight
scope or role checks after authentication. Scopes are read from a principal's
``scopes`` or ``roles`` attribute/key, either as a space-delimited string or an
iterable. Missing scopes return ``403`` and OpenAPI security requirements include
the required scopes::

    admin = bearer.requires("items:write")

    @api.post("/items", auth=admin)
    def create_item(req, resp, *, user):
        resp.media = {"user": user}


Background Queue
----------------

Run tasks in background threads without blocking the response. Available
as ``api.background``::

    @api.route("/submit")
    async def submit(req, resp):
        data = await req.media()

        @api.background.task
        def process(data):
            # runs in a thread pool
            ...

        process(data)
        resp.media = {"status": "accepted"}

.. autoclass:: responder.background.BackgroundQueue
    :members:


Query Dict
----------

A dictionary subclass for query string parameters with multi-value support.
Behaves like a normal dict for single values, but supports ``getlist()``
for parameters that appear multiple times (e.g. ``?tag=a&tag=b``).

.. autoclass:: responder.models.QueryDict
    :members:


Headers Dict
------------

The case-insensitive (case-preserving) mapping behind ``req.headers`` and
``resp.headers``. Lookups match header names case-insensitively; on request
headers, single-value access returns the last value received for a repeated
header, while ``get_list()`` returns every raw line, in order::

    hops = req.headers.get_list("X-Forwarded-For")
    # ["203.0.113.7", "198.51.100.2"] — one entry per proxy hop

.. autoclass:: responder.models.CaseInsensitiveDict
    :members: get_list


Rate Limiter
------------

Sliding-window rate limiter (fixed-window with the Redis backends). Limits
requests per client IP address — or per anything, via ``key=`` — and returns
``429 Too Many Requests`` when exceeded::

    from responder.ext.ratelimit import RateLimiter

    limiter = RateLimiter(requests=100, period=60)  # 100 req/min
    limiter.install(api)

Response headers: ``X-RateLimit-Limit``, ``X-RateLimit-Remaining``,
``X-RateLimit-Reset`` (seconds until the window resets), and ``Retry-After``
(when limited).

Pass ``key=`` (a ``req -> str`` callable) to bucket by API key or user
instead of client IP, and ``fail_open=True`` to let requests through with a
warning when the backend is unreachable (the default answers ``503``).

The in-memory backend is per-process. For multi-worker or distributed deploys,
pass a shared store via ``backend=`` —
:class:`~responder.ext.ratelimit.RedisBackend` (sync) or
:class:`~responder.ext.ratelimit.AsyncRedisBackend` (async, via
``redis.asyncio``)::

    from responder.ext.ratelimit import RateLimiter, AsyncRedisBackend

    limiter = RateLimiter(requests=100, period=60, backend=AsyncRedisBackend())

.. autoclass:: responder.ext.ratelimit.RateLimiter
    :members:


Status Code Helpers
-------------------

Convenience functions for checking which category a status code falls
into. Useful in middleware and after-request hooks::

    from responder.status_codes import is_200, is_400, is_500

    @api.after_request()
    def log_errors(req, resp):
        if is_400(resp.status_code) or is_500(resp.status_code):
            print(f"Error: {req.method} {req.url.path} -> {resp.status_code}")

.. autofunction:: responder.status_codes.is_100

.. autofunction:: responder.status_codes.is_200

.. autofunction:: responder.status_codes.is_300

.. autofunction:: responder.status_codes.is_400

.. autofunction:: responder.status_codes.is_500


Errors and Exceptions
---------------------

Use :func:`~responder.abort` to short-circuit a request with a rendered HTTP
error from anywhere in a handler, hook, or dependency — no Starlette import
required. Unlike setting ``resp.status_code``, it halts the handler::

    from responder import abort

    @api.route("/admin")
    def admin(req, resp):
        if not req.session.get("is_admin"):
            abort(403, detail="Forbidden")

.. autofunction:: responder.abort

For typed problem catalogs, raise :class:`~responder.Problem` — an
``HTTPException`` subclass carrying the full set of RFC 9457 members
(``type``, ``title``, ``instance``, and arbitrary extension members) into the
rendered ``application/problem+json`` payload. It flows through the same
machinery as framework errors, including ``API(problem_handler=...)``
enrichment and the app's JSON encoder::

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

Passing any RFC 9457 member to :func:`~responder.abort` raises a ``Problem``
for you, so ``abort(409, type="...", balance=0)`` is equivalent.

.. autoclass:: responder.Problem

Dependency injection raises the following at request time when a provider graph
is misconfigured — cycles, illegal scopes, or unresolvable parameters. Catch
the base :class:`~responder.DependencyError` to cover all four. (Registration
mistakes, such as a reserved name or a bad scope, raise plain ``ValueError``
instead.) See the :doc:`tour` for the dependency-injection guide.

.. autoexception:: responder.DependencyError

.. autoexception:: responder.DependencyCycleError

.. autoexception:: responder.DependencyScopeError

.. autoexception:: responder.DependencyResolutionError

The sessions extension raises
:class:`~responder.ext.sessions.SessionConfigError` for an unsafe or
contradictory configuration — for example ``secret_key="NOTASECRET"``, or
``sessions=True`` with no key set.

.. autoexception:: responder.ext.sessions.SessionConfigError


Type Aliases
------------

Convenience aliases in :mod:`responder.types` for annotating your own
handlers, hooks, and dependency providers::

    from responder.types import Handler, Hook, Dependency

.. autodata:: responder.types.Handler

.. autodata:: responder.types.Hook

.. autodata:: responder.types.Dependency
