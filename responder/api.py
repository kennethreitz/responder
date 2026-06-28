import functools
import inspect
import logging
import os
from pathlib import Path

__all__ = ["API"]

import uvicorn
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import State
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.errors import ServerErrorMiddleware
from starlette.middleware.exceptions import ExceptionMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.responses import Response as StarletteResponse

from . import status_codes
from .background import BackgroundQueue
from .formats import get_formats
from .models import Request, Response
from .routes import Router
from .staticfiles import StaticFiles
from .statics import DEFAULT_CORS_PARAMS, DEFAULT_OPENAPI_THEME, DEFAULT_SECRET_KEY
from .templates import Templates

logger = logging.getLogger("responder")


async def _negotiated_http_error(request, exc):
    """Render HTTPExceptions (404s and friends) as JSON for JSON clients."""
    headers = getattr(exc, "headers", None)
    if exc.status_code in (204, 304):
        return StarletteResponse(status_code=exc.status_code, headers=headers)
    if "json" in request.headers.get("accept", ""):
        return JSONResponse(
            {"error": exc.detail}, status_code=exc.status_code, headers=headers
        )
    return PlainTextResponse(exc.detail, status_code=exc.status_code, headers=headers)


def _read_text_if_exists(path: Path) -> str | None:
    """Return the file's text, or ``None`` if it doesn't exist (runs in a thread)."""
    try:
        return path.read_text()
    except FileNotFoundError:
        return None


def abort(status_code, *, detail=None, headers=None):
    """Short-circuit the request with an HTTP error response.

    Raises an ``HTTPException`` that Responder renders (as JSON or text per the
    client's ``Accept`` header). Use it to bail out from anywhere in a handler
    or dependency without importing Starlette directly.

    Usage::

        from responder import abort

        @api.route("/admin")
        def admin(req, resp):
            if not req.session.get("is_admin"):
                abort(403, detail="Forbidden")

    :param status_code: The HTTP status code (e.g. ``404``).
    :param detail: Optional error message; defaults to the status phrase.
    :param headers: Optional dict of headers to attach to the error response.
    """
    raise HTTPException(status_code=status_code, detail=detail, headers=headers)


class API:
    """The primary web-service class.

    :param static_dir: The directory to use for static files. Will be created for you if it doesn't already exist.
    :param templates_dir: The directory to use for templates. Will be created for you if it doesn't already exist.
    :param auto_escape: If ``True``, HTML and XML templates will automatically be escaped.
    :param enable_hsts: If ``True``, send all responses to HTTPS URLs.
    :param gzip: If ``True`` (the default), compress responses with GZip.
    :param openapi_theme: OpenAPI documentation theme, must be one of ``elements``, ``rapidoc``, ``redoc``, ``swagger_ui``
    """  # noqa: E501

    status_codes = status_codes

    def __init__(
        self,
        *,
        debug=False,
        title=None,
        version=None,
        description=None,
        terms_of_service=None,
        contact=None,
        license=None,  # noqa: A002
        openapi=None,
        openapi_route="/schema.yml",
        static_dir="static",
        static_route="/static",
        templates_dir="templates",
        auto_escape=True,
        secret_key=DEFAULT_SECRET_KEY,
        enable_hsts=False,
        docs_route=None,
        cors=False,
        cors_params=DEFAULT_CORS_PARAMS,
        allowed_hosts=None,
        openapi_theme=DEFAULT_OPENAPI_THEME,
        lifespan=None,
        gzip=True,
        request_id=False,
        enable_logging=False,
        redirect_slashes=True,
        max_request_size=None,
        auto_etag=False,
        request_timeout=None,
        session_backend=None,
        session_cookie=None,
        session_https_only=False,
        session_same_site="lax",
        metrics_route=None,
        encoder=None,
    ):
        """Create a new Responder API instance.

        :param debug: If ``True``, enable debug mode with verbose error pages.
        :param title: The title of the API, used in OpenAPI documentation.
        :param version: The version string for the API (e.g. ``"1.0"``).
        :param description: A longer description of the API for OpenAPI docs.
        :param terms_of_service: URL to the API's terms of service.
        :param contact: Contact information dict for the API (``name``, ``url``, ``email``).
        :param license: License information dict (``name``, ``url``).
        :param openapi: The OpenAPI version string (e.g. ``"3.0.2"``). Enables OpenAPI schema generation.
        :param openapi_route: The URL path for the OpenAPI schema (default ``"/schema.yml"``).
        :param static_dir: Directory for static files. Set to ``None`` to disable. Created automatically if missing.
        :param static_route: URL prefix for serving static files (default ``"/static"``).
        :param templates_dir: Directory for Jinja2 templates (default ``"templates"``).
        :param auto_escape: If ``True``, auto-escape HTML/XML in templates.
        :param secret_key: Secret key for signing cookie-based sessions. **Always set this in production.**
        :param enable_hsts: If ``True``, redirect all HTTP requests to HTTPS.
        :param docs_route: URL path for interactive API docs (e.g. ``"/docs"``). Enables OpenAPI if not already set.
        :param cors: If ``True``, enable CORS middleware.
        :param cors_params: Dict of CORS configuration (``allow_origins``, ``allow_methods``, etc.).
        :param allowed_hosts: List of allowed hostnames (e.g. ``["example.com"]``). Defaults to ``["*"]``.
        :param openapi_theme: Documentation UI theme: ``"swagger_ui"``, ``"redoc"``, ``"rapidoc"``, or ``"elements"``.
        :param lifespan: An async context manager for startup/shutdown logic.
        :param gzip: If ``True`` (the default), compress responses with GZip.
        :param request_id: If ``True``, add ``X-Request-ID`` headers to all responses.
        :param enable_logging: If ``True``, enable structured logging with per-request context (request ID, method, path, client IP).
        :param redirect_slashes: If ``True`` (the default), requests that miss only by a trailing slash are redirected (``307``) to the matching route.
        :param max_request_size: Maximum request body size in bytes. Bodies larger than this get a ``413`` response. ``None`` (the default) means unlimited.
        :param auto_etag: If ``True``, GET responses automatically get a content-hash ``ETag`` and matching ``If-None-Match`` requests receive ``304 Not Modified``.
        :param request_timeout: Seconds a handler may run before the request is answered with ``504 Gateway Timeout``. ``None`` (the default) means unlimited.
        :param session_backend: Store session data server-side (e.g. ``MemorySessionBackend()``, ``RedisSessionBackend()`` from ``responder.ext.sessions``) with only an opaque ID in the cookie. ``None`` (the default) keeps signed cookie-payload sessions.
        :param session_cookie: Name of the session cookie. ``None`` (the default) keeps the underlying middleware's default name.
        :param session_https_only: If ``True``, mark the session cookie ``Secure`` (only sent over HTTPS). Defaults to ``False``.
        :param session_same_site: ``SameSite`` policy for the session cookie: ``"lax"`` (default), ``"strict"``, or ``"none"``.
        :param metrics_route: URL path (e.g. ``"/metrics"``) serving request counts and latency histograms in Prometheus text format.
        :param encoder: Optional ``obj -> serializable`` callable applied across **all** response formats (JSON, YAML, MessagePack) to serialize otherwise-unsupported types. Tried first, then falls back to the built-in conversions for ``datetime``, ``UUID``, ``Decimal``, ``set``, dataclasses, and Pydantic models.
        """  # noqa: E501
        self.background = BackgroundQueue()

        self.secret_key = secret_key

        #: Application-level state. Set values at startup, read them anywhere
        #: (handlers can reach it via ``req.api.state``).
        self.state = State()

        self.formats = get_formats(encoder=encoder)

        self.router = Router(
            lifespan=lifespan,
            formats=self.formats,
            redirect_slashes=redirect_slashes,
            max_request_size=max_request_size,
            auto_etag=auto_etag,
            request_timeout=request_timeout,
        )
        self.router.api = self

        if static_dir is not None:
            if static_route is None:
                static_route = ""
            static_dir = Path(static_dir).resolve()

        self.static_dir = static_dir
        self.static_route = static_route

        self.hsts_enabled = enable_hsts
        self.cors = cors
        self.cors_params = cors_params
        self.debug = debug

        if not allowed_hosts:
            allowed_hosts = ["*"]
        self.allowed_hosts = allowed_hosts

        if self.static_dir is not None:
            self.static_dir.mkdir(parents=True, exist_ok=True)
            self.mount(self.static_route, self.static_app)

        self._session = None

        self.default_endpoint = None
        self.app = ExceptionMiddleware(self.router, debug=debug)
        self.app.add_exception_handler(HTTPException, _negotiated_http_error)

        if metrics_route:
            from .ext.metrics import MetricsCollector, MetricsMiddleware

            self.metrics = MetricsCollector()
            # Added first, so it sits just outside the exception middleware
            # and observes error responses with their real status codes.
            self.add_middleware(MetricsMiddleware, collector=self.metrics)

            def _metrics_view(req, resp):
                resp.headers["Content-Type"] = "text/plain; version=0.0.4"
                resp.content = self.metrics.render()

            self.add_route(metrics_route, _metrics_view, static=False)

        if gzip:
            self.add_middleware(GZipMiddleware)

        if self.hsts_enabled:
            self.add_middleware(HTTPSRedirectMiddleware)

        self.add_middleware(TrustedHostMiddleware, allowed_hosts=self.allowed_hosts)

        if self.cors:
            self.add_middleware(CORSMiddleware, **self.cors_params)
        self.add_middleware(ServerErrorMiddleware, debug=debug)

        if session_backend is not None:
            from .ext.sessions import ServerSessionMiddleware

            server_session_opts = {
                "https_only": session_https_only,
                "same_site": session_same_site,
            }
            if session_cookie is not None:
                server_session_opts["cookie_name"] = session_cookie
            self.add_middleware(
                ServerSessionMiddleware, backend=session_backend, **server_session_opts
            )
        else:
            if self.secret_key == DEFAULT_SECRET_KEY and not debug:
                logger.warning(
                    "Responder is signing session cookies with the built-in "
                    "default secret key, which is publicly known — anyone can "
                    "forge session data. Pass API(secret_key=...) a private, "
                    "random value in production."
                )
            cookie_session_opts = {
                "https_only": session_https_only,
                "same_site": session_same_site,
            }
            if session_cookie is not None:
                cookie_session_opts["session_cookie"] = session_cookie
            self.add_middleware(
                SessionMiddleware, secret_key=self.secret_key, **cookie_session_opts
            )


        if openapi or docs_route:
            try:
                from .ext.openapi import OpenAPISchema
            except ImportError as ex:
                raise ImportError(
                    "The dependencies for the OpenAPI extension are not installed. "
                    "Install them using: pip install responder"
                ) from ex

            self.openapi = OpenAPISchema(
                app=self,
                title=title,
                version=version,
                openapi=openapi,
                docs_route=docs_route,
                description=description,
                terms_of_service=terms_of_service,
                contact=contact,
                license=license,
                openapi_route=openapi_route,
                static_route=static_route,
                openapi_theme=openapi_theme,
            )

        self.templates = Templates(directory=templates_dir, autoescape=auto_escape)

        if request_id and not enable_logging:
            import uuid as _uuid

            def _add_request_id(req, resp):
                rid = req.headers.get("X-Request-ID", str(_uuid.uuid4()))
                resp.headers["X-Request-ID"] = rid

            self.router.after_request(_add_request_id)

        if enable_logging:
            import logging as _logging

            from .ext.logging import LoggingMiddleware, get_logger, setup_logging

            log_level = _logging.DEBUG if debug else _logging.INFO
            setup_logging(level=log_level)
            self.add_middleware(LoggingMiddleware)
            self.log = get_logger("responder.app")
        else:
            import logging as _logging

            self.log = _logging.getLogger("responder.app")

    @property
    def requests(self):
        """A test client connected to the ASGI app. Lazily initialized."""
        return self.session()

    @property
    def static_app(self):
        """The Starlette ``StaticFiles`` application for serving static assets."""
        if not hasattr(self, "_static_app"):
            assert self.static_dir is not None
            self._static_app = StaticFiles(directory=self.static_dir)
        return self._static_app

    def before_request(self, websocket=False):
        """Register a function to run before every request.

        If the hook sets ``resp.status_code``, the route handler is skipped
        and the response is sent immediately (short-circuiting).

        :param websocket: If ``True``, register as a WebSocket before-request hook instead of HTTP.

        Usage::

            @api.before_request()
            def check_auth(req, resp):
                if "Authorization" not in req.headers:
                    resp.status_code = 401
                    resp.media = {"error": "unauthorized"}

        """  # noqa: E501

        # Allow both @api.before_request and @api.before_request().
        if callable(websocket):
            f = websocket
            self.router.before_request(f, websocket=False)
            return f

        def decorator(f):
            self.router.before_request(f, websocket=websocket)
            return f

        return decorator

    def dependency(self, name=None, *, scope="request"):
        """Register a dependency provider, injected into views by parameter name.

        Any view parameter (beyond ``req`` and ``resp``) whose name matches a
        registered dependency receives the provider's value. Providers may be
        sync or async functions, or generators — code after ``yield`` runs as
        teardown once the response is sent. Providers accepting a parameter
        receive the current :class:`Request`. Each dependency is resolved at
        most once per request. Path parameters take precedence over
        dependencies of the same name.

        :param name: The injection name. Defaults to the provider's ``__name__``.
        :param scope: ``"request"`` (default) resolves per request;
                      ``"app"`` resolves once on first use and caches for the
                      application's lifetime — generator teardown then runs at
                      shutdown. App-scoped providers cannot take parameters.

        Usage::

            @api.dependency()
            async def db():
                conn = await create_connection()
                yield conn
                await conn.close()

            @api.route("/users/{id:int}")
            async def get_user(req, resp, *, id, db):
                resp.media = await db.fetch_user(id)

        An app-scoped dependency, shared across all requests::

            @api.dependency(scope="app")
            async def pool():
                pool = await create_pool()
                yield pool
                await pool.close()  # runs at application shutdown

        """
        if callable(name):  # Used as a bare decorator: @api.dependency
            self.router.add_dependency(name.__name__, name)
            return name

        def decorator(f):
            self.router.add_dependency(name or f.__name__, f, scope=scope)
            return f

        return decorator

    def add_dependency(self, name, provider, *, scope="request"):
        """Register a dependency provider under an explicit name.

        :param name: The view parameter name to inject as.
        :param provider: The provider function (sync/async function or generator).
        :param scope: ``"request"`` (default) or ``"app"``.
        """
        self.router.add_dependency(name, provider, scope=scope)

    def after_request(self, f=None):
        """Register a function to run after every request.

        Works both bare and called: ``@api.after_request`` or
        ``@api.after_request()``.

        Usage::

            @api.after_request
            def add_request_id(req, resp):
                resp.headers["X-Request-ID"] = str(uuid.uuid4())

        """
        if callable(f):  # used as a bare decorator: @api.after_request
            self.router.after_request(f)
            return f

        def decorator(func):
            self.router.after_request(func)
            return func

        return decorator

    def add_middleware(self, middleware_cls, **middleware_config):
        """Add ASGI middleware to the application.

        Middleware wraps the entire application and can inspect or modify
        every request and response. Middleware is applied in reverse order —
        the last middleware added runs first.

        :param middleware_cls: A Starlette-compatible middleware class.
        :param middleware_config: Keyword arguments passed to the middleware constructor.

        Usage::

            from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
            api.add_middleware(HTTPSRedirectMiddleware)

        """
        self.app = middleware_cls(self.app, **middleware_config)

    def exception_handler(self, exception_cls):
        """Register a handler for a specific exception type.

        Usage::

            @api.exception_handler(ValueError)
            async def handle_value_error(req, resp, exc):
                resp.status_code = 400
                resp.media = {"error": str(exc)}

        """

        def decorator(func):
            async def _handler(request, exc):
                from starlette.responses import Response as StarletteResp

                req = Request(
                    request.scope, request.receive, api=self, formats=self.formats
                )
                resp = Response(req=req, formats=self.formats)
                if inspect.iscoroutinefunction(func):
                    await func(req, resp, exc)
                else:
                    func(req, resp, exc)
                if resp.status_code is None:
                    resp.status_code = 500
                body, headers = await resp.body
                return StarletteResp(
                    content=body, status_code=resp.status_code, headers=headers
                )

            # Register on the ExceptionMiddleware in the ASGI app chain
            from starlette.middleware.exceptions import ExceptionMiddleware as EM

            app = self.app
            while app is not None:
                if isinstance(app, EM):
                    app.add_exception_handler(exception_cls, _handler)
                    break
                app = getattr(app, "app", None)
            return func

        return decorator

    def schema(self, name, **options):
        """
        Decorator for creating new routes around function and class definitions.

        Usage::

            from marshmallow import Schema, fields
            @api.schema("Pet")
            class PetSchema(Schema):
                name = fields.Str()
        """

        def decorator(f):
            self.openapi.add_schema(name=name, schema=f, **options)
            return f

        return decorator

    def path_matches_route(self, path):
        """Given a path portion of a URL, tests that it matches against any registered route.

        :param path: The path portion of a URL, to test all known routes against.
        """  # noqa: E501 (Line too long)
        for route in self.router.routes:
            match, _ = route.matches(path)
            if match:
                return route
        return None

    def add_route(
        self,
        route=None,
        endpoint=None,
        *,
        default=False,
        static=True,
        check_existing=True,
        websocket=False,
        before_request=False,
        methods=None,
    ):
        """Adds a route to the API.

        :param route: A string representation of the route.
        :param endpoint: The endpoint for the route -- can be a callable, or a class.
        :param default: If ``True``, all unknown requests will route to this view.
        :param static: If ``True``, and no endpoint was passed, render "static/index.html".
                       Also, it will become a default route.
        :param methods: Optional list of HTTP methods (e.g. ``["GET", "POST"]``).
        """  # noqa: E501

        if static and not endpoint:
            if self.static_dir is None:
                raise ValueError(
                    "Cannot add a static fallback route: static_dir is disabled"
                )
            endpoint = self._static_response
            default = True

        self.router.add_route(
            route,
            endpoint,
            default=default,
            websocket=websocket,
            before_request=before_request,
            check_existing=check_existing,
            methods=methods,
        )

    async def _static_response(self, req, resp):
        assert self.static_dir is not None

        index = (self.static_dir / "index.html").resolve()
        # Read off the event loop — this runs from an async dispatch path.
        contents = await run_in_threadpool(_read_text_if_exists, index)
        if contents is not None:
            resp.html = contents
        else:
            resp.status_code = status_codes.HTTP_404  # type: ignore[attr-defined]
            resp.text = "Not found."

    def redirect(
        self,
        resp,
        location,
        *,
        set_text=True,
        status_code=status_codes.HTTP_301,  # type: ignore[attr-defined]
        allow_external=True,
    ):
        """
        Redirects a given response to a given location.

        :param resp: The Response to mutate.
        :param location: The location of the redirect.
        :param set_text: If ``True``, sets the Redirect body content automatically.
        :param status_code: an `API.status_codes` attribute, or an integer,
                            representing the HTTP status code of the redirect.
        :param allow_external: If ``False``, refuse (with a ``400``) to redirect to
                            an external URL — pass this for user-supplied locations.
        """
        resp.redirect(
            location,
            set_text=set_text,
            status_code=status_code,
            allow_external=allow_external,
        )

    def on_event(self, event_type: str, **args):
        """Decorator for registering functions or coroutines to run at certain events
        Supported events: startup, shutdown

        Usage::

            @api.on_event('startup')
            async def open_database_connection_pool():
                ...

            @api.on_event('shutdown')
            async def close_database_connection_pool():
                ...

        """

        def decorator(func):
            self.add_event_handler(event_type, func, **args)
            return func

        return decorator

    def add_event_handler(self, event_type, handler):
        """Adds an event handler to the API.

        :param event_type: A string in ("startup", "shutdown")
        :param handler: The function to run. Can be either a function or a coroutine.
        """

        self.router.add_event_handler(event_type, handler)

    def route(
        self,
        route=None,
        *,
        request_model=None,
        response_model=None,
        params_model=None,
        **options,
    ):
        """Decorator for creating new routes around function and class definitions.

        Usage::

            @api.route("/hello")
            def hello(req, resp):
                resp.text = "hello, world!"

        With Pydantic models for validation and OpenAPI documentation::

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

        Query parameters validate the same way with ``params_model`` —
        invalid queries get a ``422``, valid ones land on
        ``req.state.validated_params``::

            class SearchParams(BaseModel):
                q: str
                limit: int = 10

            @api.route("/search", params_model=SearchParams)
            async def search(req, resp):
                params = req.state.validated_params
                resp.media = {"q": params.q, "limit": params.limit}

        """

        def decorator(f):
            if request_model is not None:
                f._request_model = request_model
                if hasattr(self, "openapi"):
                    self.openapi.add_schema(
                        request_model.__name__, request_model, check_existing=False
                    )
            if response_model is not None:
                f._response_model = response_model
                if hasattr(self, "openapi"):
                    self.openapi.add_schema(
                        response_model.__name__, response_model, check_existing=False
                    )
            if params_model is not None:
                f._params_model = params_model
            self.add_route(route, f, **options)
            return f

        return decorator

    def graphql(
        self,
        route="/graphql",
        *,
        schema,
        graphiql=True,
        introspection=True,
        max_depth=None,
    ):
        """Mount a GraphQL API at the given route.

        Usage::

            import graphene

            class Query(graphene.ObjectType):
                hello = graphene.String(name=graphene.String(default_value="stranger"))
                def resolve_hello(self, info, name):
                    return f"Hello {name}"

            api.graphql("/graphql", schema=graphene.Schema(query=Query))

        For production, disable the in-browser IDE and introspection and cap
        query depth::

            api.graphql(
                "/graphql", schema=schema,
                graphiql=api.debug, introspection=api.debug, max_depth=10,
            )

        :param route: The URL path for the GraphQL endpoint.
        :param schema: A Graphene schema instance.
        :param graphiql: Serve the in-browser GraphiQL IDE for HTML ``GET``
                         requests (default ``True``).
        :param introspection: Allow schema-introspection queries (default ``True``).
        :param max_depth: Reject queries nested deeper than this (default unlimited).
        """
        from .ext.graphql import GraphQLView

        self.add_route(
            route,
            GraphQLView(
                api=self,
                schema=schema,
                graphiql=graphiql,
                introspection=introspection,
                max_depth=max_depth,
            ),
        )

    def mount(self, route, app):
        """Mounts an WSGI / ASGI application at a given route.

        :param route: String representation of the route to be used
                      (shouldn't be parameterized).
        :param app: The other WSGI / ASGI app.
        """
        self.router.apps.update({route: app})

    def session(self, base_url="http://;"):
        """Testing HTTP client. Returns a Starlette TestClient instance,
        able to send HTTP requests to the Responder application.

        :param base_url: The base URL for the test client.
        """

        if self._session is None:
            from starlette.testclient import TestClient

            self._session = TestClient(self, base_url=base_url)
        return self._session

    def url_for(self, endpoint, **params):
        """Given an endpoint, returns a rendered URL for its route.

        :param endpoint: The route endpoint you're searching for.
        :param params: Data to pass into the URL generator (for parameterized URLs).
        """
        return self.router.url_for(endpoint, **params)

    def template(self, filename, *args, **kwargs):
        r"""Render a Jinja2 template file with the provided values.

        :param filename: The filename of the jinja2 template, in ``templates_dir``.
        :param \*args: Data to pass into the template.
        :param \*\*kwargs: Data to pass into the template.
        """
        return self.templates.render(filename, *args, **kwargs)

    def template_string(self, source, *args, **kwargs):
        r"""Render a Jinja2 template string with the provided values.

        :param source: The template to use, a Jinja2 template string.
        :param \*args: Data to pass into the template.
        :param \*\*kwargs: Data to pass into the template.
        """
        return self.templates.render_string(source, *args, **kwargs)

    def serve(self, *, address=None, port=None, debug=False, **options):
        """
        Run the application with uvicorn.

        If the ``PORT`` environment variable is set, requests will be served on that port
        automatically to all known hosts.

        :param address: The address to bind to.
        :param port: The port to bind to. If none is provided, one will be selected at random.
        :param debug: Whether to run application in debug mode.
        :param options: Additional keyword arguments to send to ``uvicorn.run()``.
        """  # noqa: E501

        if "PORT" in os.environ:
            if address is None:
                address = "0.0.0.0"  # noqa: S104
            port = int(os.environ["PORT"])

        if address is None:
            address = "127.0.0.1"
        if port is None:
            port = 5042
        if debug:
            options["log_level"] = "debug"

        uvicorn.run(self, host=address, port=port, **options)

    def run(self, **kwargs):
        """Run the application. Shorthand for :meth:`serve` that inherits the ``debug`` setting.

        :param kwargs: Keyword arguments passed through to :meth:`serve`.
        """  # noqa: E501
        if "debug" not in kwargs:
            kwargs.update({"debug": self.debug})
        self.serve(**kwargs)

    def group(self, prefix):
        """Create a route group with a shared URL prefix.

        Usage::

            v1 = api.group("/v1")

            @v1.route("/users")
            def list_users(req, resp):
                resp.media = []

            @v1.route("/users/{id:int}")
            def get_user(req, resp, *, id):
                resp.media = {"id": id}

        """
        return RouteGroup(api=self, prefix=prefix)

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)


class RouteGroup:
    """A group of routes with a shared URL prefix.

    Before-request hooks registered on a group only run for requests whose
    path falls under the group's prefix.
    """

    def __init__(self, api, prefix):
        self.api = api
        self.prefix = prefix.rstrip("/")

    def route(self, route=None, **options):
        full_route = f"{self.prefix}{route}"
        return self.api.route(full_route, **options)

    def _path_in_group(self, path):
        return path == self.prefix or path.startswith(self.prefix + "/")

    def before_request(self, websocket=False):
        """Register a hook that runs before requests under this group's prefix."""

        def decorator(f):
            if inspect.iscoroutinefunction(f):

                async def hook(target, *rest):
                    if self._path_in_group(target.url.path):
                        await f(target, *rest)

            else:

                def hook(target, *rest):
                    if self._path_in_group(target.url.path):
                        f(target, *rest)

            functools.wraps(f)(hook)
            self.api.router.before_request(hook, websocket=websocket)
            return f

        return decorator
