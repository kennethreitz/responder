Writing Middleware
==================

Middleware sits between the server and your route handlers, processing
every request and response that flows through your application. It's the
right tool for cross-cutting concerns — things that apply to *all*
requests, not just specific routes.

Common middleware use cases:

- Request logging and timing
- Authentication and authorization
- Adding security headers
- Request ID generation
- Rate limiting
- Response compression (built-in)


Hooks vs. Middleware
--------------------

Responder gives you two levels of request processing:

**Hooks** (``before_request`` / ``after_request``) run inside Responder's
routing layer. They receive Responder's ``req`` and ``resp`` objects and
are the simplest way to add behavior::

    @api.route(before_request=True)
    def add_header(req, resp):
        resp.headers["X-Powered-By"] = "Responder"

    @api.after_request()
    def log_request(req, resp):
        print(f"{req.method} {req.url.path} -> {resp.status_code}")

.. note::

   ``req.method`` is uppercase (``"GET"``, ``"POST"``, …). Compare against
   uppercase strings — a lowercase check like ``req.method == "get"`` still
   works but is deprecated, and ``req.method in {"get"}`` silently misses.

**Middleware** runs at the ASGI level, wrapping the entire application.
It's more powerful but more complex — you work with raw ASGI scopes
instead of Responder objects. Use middleware when you need to process
requests *before* they reach Responder's routing, or when you need to
integrate with Starlette middleware.


Using Starlette Middleware
--------------------------

Responder is built on Starlette, so any Starlette middleware works
out of the box::

    from starlette.middleware.base import BaseHTTPMiddleware

    class TimingMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            import time
            start = time.time()
            response = await call_next(request)
            duration = time.time() - start
            response.headers["X-Response-Time"] = f"{duration:.3f}s"
            return response

    api.add_middleware(TimingMiddleware)

The ``dispatch`` method receives a Starlette ``Request`` and a
``call_next`` function. Call ``call_next(request)`` to pass the request
to the next middleware (or to your route handler). The return value is
a Starlette ``Response`` that you can modify before it's sent.


Built-in Middleware
-------------------

Every Responder app ships with a small stack wired up for you:

- **ServerErrorMiddleware** — catches unhandled exceptions and renders a 500
- **ExceptionMiddleware** — routes ``HTTPException``\ s and status codes to your handlers
- **TrustedHostMiddleware** — validates the ``Host`` header (``["*"]`` by default)
- **GZipMiddleware** — compresses responses larger than 500 bytes (on by default)

A few more are wired in on demand, by constructor flag:

- **SessionMiddleware** — signed cookie sessions, on unless you pass
  ``sessions=False``. Secure by default: the signing key never falls back to a
  public default, cookies are ``Secure`` in production, and ``req.session`` /
  ``resp.session`` raise ``RuntimeError`` when sessions are off. See
  :doc:`guide-config` for the full story.
- **CORSMiddleware** — ``responder.API(cors=True)``
- **HTTPSRedirectMiddleware** — ``responder.API(enable_hsts=True)``
- **RequestIDMiddleware** — ``responder.API(request_id=True)`` adds an
  ``X-Request-ID`` header to every response
- **LoggingMiddleware** — ``responder.API(enable_logging=True)`` for structured
  per-request logging (it handles request IDs itself, superseding ``request_id``)
- **MetricsMiddleware** — ``responder.API(metrics_route="/metrics")`` exposes
  Prometheus metrics

The observability options (request IDs, logging, metrics) are covered in
:doc:`tour`.


Adding Third-Party Middleware
-----------------------------

Any ASGI middleware can be added with ``api.add_middleware()``::

    from some_package import SomeMiddleware

    api.add_middleware(SomeMiddleware, option1="value", option2=True)

Keyword arguments are passed to the middleware's constructor.

Middleware can be registered any time before the first request, not only at
construction — the ASGI stack is assembled lazily and rebuilt whenever you add
more. That assembled stack is exposed as the read-only ``api.app`` property, so
you can't inject middleware by assigning to it. Use ``api.add_middleware()``, or
wrap the API object itself for a truly outermost layer (see `Middleware Order`_).


Middleware Order
----------------

Middleware wraps your application like the layers of an onion. A request
travels inward through every layer to your route, and the response travels
back outward in reverse.

The full built-in stack, from outermost to innermost, is:

1. **LoggingMiddleware** (``enable_logging=True``) *or* **RequestIDMiddleware**
   (``request_id=True``) — the observability tier. It wraps everything below,
   so even a rendered 500 carries its ``X-Request-ID`` and real status.
2. **MetricsMiddleware** (``metrics_route=...``)
3. **ServerErrorMiddleware** — the outermost *application* layer; it catches
   errors from every middleware and route beneath it.
4. **your middleware** (added with ``add_middleware``)
5. **TrustedHostMiddleware**
6. **HTTPSRedirectMiddleware** (``enable_hsts=True``)
7. **CORSMiddleware** (``cors=True``)
8. **SessionMiddleware** (unless ``sessions=False``)
9. **GZipMiddleware** (on by default)
10. **ExceptionMiddleware** — routes non-500 exceptions to your handlers
11. **your routes**

Two consequences worth knowing:

- Your middleware sits *inside* ``ServerErrorMiddleware``, so an exception it
  raises is caught and rendered as a 500 instead of crashing the server.
- Sessions sit beneath ``ServerErrorMiddleware``, so they are *not* persisted on
  an unhandled 500.

``api.add_middleware()`` inserts your middleware just inside
``ServerErrorMiddleware`` — *not* at the very top of the stack. Among your own
middleware, the most-recently-added is the outermost and runs first, so if
middleware A depends on B having run first, add B before A.

To wrap *everything* — including error rendering and the observability tier —
wrap the API object itself::

    asgi = MyOutermostMiddleware(api)

That ``asgi`` callable is what you then serve.


Writing Pure ASGI Middleware
----------------------------

For maximum performance and control, you can write middleware as a plain
ASGI application. This bypasses Starlette's ``BaseHTTPMiddleware``
abstraction — it's faster and gives you direct access to the ASGI
protocol::

    class SecurityHeadersMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return

            async def send_with_headers(message):
                if message["type"] == "http.response.start":
                    extra = [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    ]
                    message["headers"] = list(message["headers"]) + extra
                await send(message)

            await self.app(scope, receive, send_with_headers)

    api.add_middleware(SecurityHeadersMiddleware)

This is the same pattern used internally by Starlette and uvicorn. The
middleware receives the ASGI ``scope``, ``receive``, and ``send`` callables,
and wraps ``send`` to inject headers into the response.

For most cases, ``BaseHTTPMiddleware`` is simpler and perfectly fine.
Use the pure ASGI approach when you need to handle WebSocket connections,
streaming responses, or want to avoid the overhead of request/response
object creation.


When to Use What
-----------------

- **Simple header additions, logging, auth checks** → use hooks
- **Response transformation, timing, third-party integrations** → use middleware
- **Rate limiting** → use the built-in ``RateLimiter`` (it uses hooks internally)
- **Request ID** → use ``api = responder.API(request_id=True)``

Start with hooks. They're simpler and cover most cases. Graduate to
middleware when hooks aren't enough.
