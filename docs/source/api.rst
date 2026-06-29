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

   ``req.method`` is an UPPERCASE string (``"GET"``, ``"POST"``), backed by
   :class:`~responder.HTTPMethod`. For one deprecation cycle it still compares
   case-insensitively (``req.method == "get"`` works, with a
   ``DeprecationWarning``), but **hash-based membership is case-sensitive** —
   ``req.method in {"get"}`` and ``{"get": ...}[req.method]`` miss silently.
   Compare with ``==`` or a tuple, or key by the uppercase form.

For reading typed query parameters, headers, and cookies straight off the
signature, see `Parameter Markers`_ below.

.. autoclass:: Request
    :inherited-members:

.. autoclass:: responder.HTTPMethod


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

    # Redirect (external targets allowed by default; pass
    # allow_external=False to refuse off-site URLs):
    resp.redirect("/dashboard")

.. note::

   Handlers can also *return* the body Flask-style instead of mutating
   ``resp``: ``return body``, ``return body, status``, or
   ``return body, status, headers``. Pydantic models and dataclasses serialize
   natively, so ``resp.media = SomeModel`` works without a trailing
   ``.model_dump()``. ``resp.session`` is a read/write view of ``req.session``
   and raises ``RuntimeError`` when the app is built with ``sessions=False``.

.. autoclass:: Response
    :inherited-members:


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
        data = await document.read()
        resp.media = {"name": document.filename, "size": len(data)}

A sequence annotation (``list[UploadFile]``) collects multiple files sent under
one field name. These routes generate a ``multipart/form-data`` (or
``application/x-www-form-urlencoded``) request body in OpenAPI, so the
interactive docs show a file picker. The legacy ``await req.media("files")``
bytes-dict is unchanged.

.. autofunction:: responder.Query

.. autofunction:: responder.Header

.. autofunction:: responder.Cookie

.. autofunction:: responder.Path

.. autofunction:: responder.Form

.. autofunction:: responder.File


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


Rate Limiter
------------

In-memory token bucket rate limiter. Limits requests per client IP address
and returns ``429 Too Many Requests`` when exceeded::

    from responder.ext.ratelimit import RateLimiter

    limiter = RateLimiter(requests=100, period=60)  # 100 req/min
    limiter.install(api)

Response headers: ``X-RateLimit-Limit``, ``X-RateLimit-Remaining``,
and ``Retry-After`` (when limited).

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
