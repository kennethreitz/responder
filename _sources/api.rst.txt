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
        secret_key="change-me",       # session signing key
        allowed_hosts=["example.com"],
    )

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

    # Headers (case-insensitive)
    token = req.headers.get("Authorization")

    # Query parameters: /search?q=python&page=2
    query = req.params["q"]

    # JSON body
    data = await req.media()

    # Form data
    form = await req.media("form")

    # File uploads
    files = await req.media("files")

    # Client info
    ip, port = req.client
    is_https = req.is_secure

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
    resp.file("path/to/file.pdf")       # auto content-type
    resp.stream_file("large/export.csv") # streamed

    resp.status_code = 201
    resp.headers["X-Custom"] = "value"
    resp.cookies["session"] = "abc123"

.. autoclass:: Response
    :inherited-members:


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
