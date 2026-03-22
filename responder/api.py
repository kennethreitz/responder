import asyncio
import os
from pathlib import Path

__all__ = ["API"]

import uvicorn
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.errors import ServerErrorMiddleware
from starlette.middleware.exceptions import ExceptionMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import status_codes
from .background import BackgroundQueue
from .formats import get_formats
from .models import Request, Response
from .routes import Router
from .staticfiles import StaticFiles
from .statics import DEFAULT_CORS_PARAMS, DEFAULT_OPENAPI_THEME, DEFAULT_SECRET_KEY
from .templates import Templates


class API:
    """The primary web-service class.

    :param static_dir: The directory to use for static files. Will be created for you if it doesn't already exist.
    :param templates_dir: The directory to use for templates. Will be created for you if it doesn't already exist.
    :param auto_escape: If ``True``, HTML and XML templates will automatically be escaped.
    :param enable_hsts: If ``True``, send all responses to HTTPS URLs.
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
    ):
        self.background = BackgroundQueue()

        self.secret_key = secret_key

        self.router = Router(lifespan=lifespan)

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

        self.formats = get_formats()

        self._session = None

        self.default_endpoint = None
        self.app = ExceptionMiddleware(self.router, debug=debug)
        self.add_middleware(GZipMiddleware)

        if self.hsts_enabled:
            self.add_middleware(HTTPSRedirectMiddleware)

        self.add_middleware(TrustedHostMiddleware, allowed_hosts=self.allowed_hosts)

        if self.cors:
            self.add_middleware(CORSMiddleware, **self.cors_params)
        self.add_middleware(ServerErrorMiddleware, debug=debug)
        self.add_middleware(SessionMiddleware, secret_key=self.secret_key)

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

        self.templates = Templates(directory=templates_dir)

    @property
    def requests(self):
        """A test client connected to the ASGI app. Lazily initialized."""
        return self.session()

    @property
    def static_app(self):
        if not hasattr(self, "_static_app"):
            assert self.static_dir is not None
            self._static_app = StaticFiles(directory=self.static_dir)
        return self._static_app

    def before_request(self, websocket=False):
        def decorator(f):
            self.router.before_request(f, websocket=websocket)
            return f

        return decorator

    def add_middleware(self, middleware_cls, **middleware_config):
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

                req = Request(request.scope, request.receive, formats=get_formats())
                resp = Response(req=req, formats=get_formats())
                if asyncio.iscoroutinefunction(func):
                    await func(req, resp, exc)
                else:
                    func(req, resp, exc)
                if resp.status_code is None:
                    resp.status_code = 500
                body, headers = await resp.body
                return StarletteResp(
                    content=body, status_code=resp.status_code, headers=headers
                )

            # Register with the ExceptionMiddleware
            self.router._exception_handlers = getattr(
                self.router, "_exception_handlers", {}
            )
            self.router._exception_handlers[exception_cls] = _handler
            # Also register on the ASGI app chain
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

        if static:
            assert self.static_dir is not None
            if not endpoint:
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
        if index.exists():
            resp.html = index.read_text()
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
    ):
        """
        Redirects a given response to a given location.

        :param resp: The Response to mutate.
        :param location: The location of the redirect.
        :param set_text: If ``True``, sets the Redirect body content automatically.
        :param status_code: an `API.status_codes` attribute, or an integer,
                            representing the HTTP status code of the redirect.
        """
        resp.redirect(location, set_text=set_text, status_code=status_code)

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

    def route(self, route=None, *, request_model=None, response_model=None, **options):
        """Decorator for creating new routes around function and class definitions.

        Usage::

            @api.route("/hello")
            def hello(req, resp):
                resp.text = "hello, world!"

        With Pydantic models for OpenAPI documentation::

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
            self.add_route(route, f, **options)
            return f

        return decorator

    def graphql(self, route="/graphql", *, schema):
        """Mount a GraphQL API at the given route.

        Usage::

            import graphene

            class Query(graphene.ObjectType):
                hello = graphene.String(name=graphene.String(default_value="stranger"))
                def resolve_hello(self, info, name):
                    return f"Hello {name}"

            api.graphql("/graphql", schema=graphene.Schema(query=Query))

        :param route: The URL path for the GraphQL endpoint.
        :param schema: A Graphene schema instance.
        """
        from .ext.graphql import GraphQLView

        self.add_route(route, GraphQLView(api=self, schema=schema))

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
        if "debug" not in kwargs:
            kwargs.update({"debug": self.debug})
        self.serve(**kwargs)

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)
