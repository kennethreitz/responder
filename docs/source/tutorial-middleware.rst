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

Responder configures several middleware components automatically:

- **GZipMiddleware** — compresses responses larger than 500 bytes
- **TrustedHostMiddleware** — validates the ``Host`` header
- **ServerErrorMiddleware** — catches unhandled exceptions
- **ExceptionMiddleware** — routes exceptions to your handlers
- **SessionMiddleware** — manages signed cookie sessions

Optional middleware you can enable:

- **CORSMiddleware** — ``api = responder.API(cors=True)``
- **HTTPSRedirectMiddleware** — ``api = responder.API(enable_hsts=True)``


Adding Third-Party Middleware
-----------------------------

Any ASGI middleware can be added with ``api.add_middleware()``::

    from some_package import SomeMiddleware

    api.add_middleware(SomeMiddleware, option1="value", option2=True)

Keyword arguments are passed to the middleware's constructor.


Middleware Order
----------------

Middleware wraps your application like layers of an onion. The *last*
middleware added is the *outermost* layer — it sees the request first
and the response last.

Responder's built-in middleware stack (from outermost to innermost):

1. SessionMiddleware
2. ServerErrorMiddleware
3. CORSMiddleware (if enabled)
4. TrustedHostMiddleware
5. HTTPSRedirectMiddleware (if enabled)
6. GZipMiddleware
7. ExceptionMiddleware
8. Your routes

When you call ``api.add_middleware()``, your middleware is added *outside*
the existing stack. Keep this in mind for ordering dependencies — if
middleware A depends on middleware B having run first, add B before A.


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
                    headers = dict(message.get("headers", []))
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
