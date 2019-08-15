import json
import os

from uuid import uuid4
from pathlib import Path
from base64 import b64encode

import apistar
import itsdangerous
import jinja2
import uvicorn
import yaml
from apispec import APISpec, yaml_utils
from apispec.ext.marshmallow import MarshmallowPlugin
from starlette.exceptions import ExceptionMiddleware
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.middleware.errors import ServerErrorMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Lifespan
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient
from starlette.websockets import WebSocket
from whitenoise import WhiteNoise

from . import models, status_codes
from .background import BackgroundQueue
from .formats import get_formats
from .routes import Router
from .statics import DEFAULT_API_THEME, DEFAULT_CORS_PARAMS, DEFAULT_SECRET_KEY
from .templates import GRAPHIQL


class API:
    """The primary web-service class.

        :param static_dir: The directory to use for static files. Will be created for you if it doesn't already exist.
        :param templates_dir: The directory to use for templates. Will be created for you if it doesn't already exist.
        :param auto_escape: If ``True``, HTML and XML templates will automatically be escaped.
        :param enable_hsts: If ``True``, send all responses to HTTPS URLs.
        :param title: The title of the application (OpenAPI Info Object)
        :param version: The version of the OpenAPI document (OpenAPI Info Object)
        :param description: The description of the OpenAPI document (OpenAPI Info Object)
        :param terms_of_service: A URL to the Terms of Service for the API (OpenAPI Info Object)
        :param contact: The contact dictionary of the application (OpenAPI Contact Object)
        :param license: The license information of the exposed API (OpenAPI License Object)
    """

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
        license=None,
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
    ):
        self.background = BackgroundQueue()

        self.secret_key = secret_key
        self.title = title
        self.version = version
        self.description = description
        self.terms_of_service = terms_of_service
        self.contact = contact
        self.license = license
        self.openapi_version = openapi

        if static_dir is not None:
            if static_route is None:
                static_route = static_dir
            static_dir = Path(os.path.abspath(static_dir))

        self.static_dir = static_dir
        self.static_route = static_route

        self.built_in_templates_dir = Path(
            os.path.abspath(os.path.dirname(__file__) + "/templates")
        )

        if templates_dir is not None:
            templates_dir = Path(os.path.abspath(templates_dir))

        self.templates_dir = templates_dir or self.built_in_templates_dir

        self.router = Router()

        self.docs_theme = DEFAULT_API_THEME
        self.docs_route = docs_route
        self.schemas = {}

        self.hsts_enabled = enable_hsts
        self.cors = cors
        self.cors_params = cors_params
        self.debug = debug

        if not allowed_hosts:
            # if not debug:
            #     raise RuntimeError(
            #         "You need to specify `allowed_hosts` when debug is set to False"
            #     )
            allowed_hosts = ["*"]
        self.allowed_hosts = allowed_hosts

        # Make the static/templates directory if they don't exist.
        for _dir in (self.static_dir, self.templates_dir):
            if _dir is not None:
                os.makedirs(_dir, exist_ok=True)

        if self.static_dir is not None:
            self.whitenoise = WhiteNoise(application=self._notfound_wsgi_app)
            self.whitenoise.add_files(str(self.static_dir))

            self.whitenoise.add_files(
                (
                    Path(apistar.__file__).parent
                    / "themes"
                    / self.docs_theme
                    / "static"
                ).resolve()
            )

            self.mount(self.static_route, self.whitenoise)

        self.formats = get_formats()

        # Cached requests session.
        self._session = None

        if self.openapi_version:
            self.add_route(openapi_route, self.schema_response)

        if self.docs_route:
            self.add_route(self.docs_route, self.docs_response)

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

        # Jinja environment
        self.jinja_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(
                [str(self.templates_dir), str(self.built_in_templates_dir)],
                followlinks=True,
            ),
            autoescape=jinja2.select_autoescape(["html", "xml"] if auto_escape else []),
        )
        self.jinja_values_base = {"api": self}  # Give reference to self.
        self.requests = (
            self.session()
        )  #: A Requests session that is connected to the ASGI app.

    @staticmethod
    def _notfound_wsgi_app(environ, start_response):
        start_response("404 NOT FOUND", [("Content-Type", "text/plain")])
        return [b"Not Found."]

    def before_request(self, websocket=False):
        def decorator(f):
            self.router.before_request(f, websocket=websocket)
            return f

        return decorator

    @property
    def before_http_requests(self):
        return self.before_requests.get("http", [])

    @property
    def before_ws_requests(self):
        return self.before_requests.get("ws", [])

    @property
    def _apispec(self):

        info = {}
        if self.description is not None:
            info["description"] = self.description
        if self.terms_of_service is not None:
            info["termsOfService"] = self.terms_of_service
        if self.contact is not None:
            info["contact"] = self.contact
        if self.license is not None:
            info["license"] = self.license

        spec = APISpec(
            title=self.title,
            version=self.version,
            openapi_version=self.openapi_version,
            plugins=[MarshmallowPlugin()],
            info=info,
        )

        for route in self.router.routes:
            if route.description:
                operations = yaml_utils.load_operations_from_docstring(
                    route.description
                )
                spec.path(path=route.route, operations=operations)

        for name, schema in self.schemas.items():
            spec.components.schema(name, schema=schema)

        return spec

    @property
    def openapi(self):
        return self._apispec.to_yaml()

    def add_middleware(self, middleware_cls, **middleware_config):
        self.app = middleware_cls(self.app, **middleware_config)

    async def __call__(self, scope, receive, send):
        await self.app(scope, receive, send)

    def add_schema(self, name, schema, check_existing=True):
        """Adds a mashmallow schema to the API specification."""
        if check_existing:
            assert name not in self.schemas

        self.schemas[name] = schema

    def schema(self, name, **options):
        """Decorator for creating new routes around function and class definitions.

        Usage::

            from marshmallow import Schema, fields

            @api.schema("Pet")
            class PetSchema(Schema):
                name = fields.Str()

        """

        def decorator(f):
            self.add_schema(name=name, schema=f, **options)
            return f

        return decorator

    def path_matches_route(self, path):
        """Given a path portion of a URL, tests that it matches against any registered route.

        :param path: The path portion of a URL, to test all known routes against.
        """
        for route in self.router.routes:
            match, _ = route.matches(path)
            if match:
                return route

    def add_route(
        self,
        route=None,
        endpoint=None,
        *,
        default=False,
        check_existing=True,
        websocket=False,
        before_request=False,
    ):
        """Adds a route to the API.

        :param route: A string representation of the route.
        :param endpoint: The endpoint for the route -- can be a callable, or a class.
        :param default: If ``True``, all unknown requests will route to this view.
        :param static: If ``True``, and no endpoint was passed, render "static/index.html", and it will become a default route.
        """
        self.router.add_route(
            route,
            endpoint,
            default=default,
            websocket=websocket,
            before_request=before_request,
        )

    def docs_response(self, req, resp):
        resp.html = self.docs

    def schema_response(self, req, resp):
        resp.status_code = status_codes.HTTP_200
        resp.headers["Content-Type"] = "application/x-yaml"
        resp.content = self.openapi

    def redirect(
        self, resp, location, *, set_text=True, status_code=status_codes.HTTP_301
    ):
        """Redirects a given response to a given location.
        :param resp: The Response to mutate.
        :param location: The location of the redirect.
        :param set_text: If ``True``, sets the Redirect body content automatically.
        :param status_code: an `API.status_codes` attribute, or an integer, representing the HTTP status code of the redirect.
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

        self.router.lifespan_handler.add_event_handler(event_type, handler)

    def route(self, route=None, **options):
        """Decorator for creating new routes around function and class definitions.

        Usage::

            @api.route("/hello")
            def hello(req, resp):
                resp.text = "hello, world!"

        """

        def decorator(f):
            self.router.add_route(route, f, **options)
            return f

        return decorator

    def mount(self, route, app):
        """Mounts an WSGI / ASGI application at a given route.

        :param route: String representation of the route to be used (shouldn't be parameterized).
        :param app: The other WSGI / ASGI app.
        """
        self.router.apps.update({route: app})

    def session(self, base_url="http://;"):
        """Testing HTTP client. Returns a Requests session object, able to send HTTP requests to the Responder application.

        :param base_url: The URL to mount the connection adaptor to.
        """

        if self._session is None:
            self._session = TestClient(self, base_url=base_url)
        return self._session

    def url_for(self, endpoint, **params):
        # TODO: Absolute_url
        """Given an endpoint, returns a rendered URL for its route.

        :param endpoint: The route endpoint you're searching for.
        :param params: Data to pass into the URL generator (for parameterized URLs).
        """
        return self.router.url_for(endpoint, **params)

    def static_url(self, asset):
        """Given a static asset, return its URL path."""
        assert None not in (self.static_dir, self.static_route)
        return f"{self.static_route}/{str(asset)}"

    @property
    def docs(self):

        loader = jinja2.PrefixLoader(
            {
                self.docs_theme: jinja2.PackageLoader(
                    "apistar", os.path.join("themes", self.docs_theme, "templates")
                )
            }
        )
        env = jinja2.Environment(autoescape=True, loader=loader)
        document = apistar.document.Document()
        document.content = yaml.safe_load(self.openapi)

        template = env.get_template("/".join([self.docs_theme, "index.html"]))

        return template.render(
            document=document,
            langs=["javascript", "python"],
            code_style=None,
            static_url=self.static_url,
            schema_url="/schema.yml",
        )

    def template(self, name_, **values):
        """Renders the given `jinja2 <http://jinja.pocoo.org/docs/>`_ template, with provided values supplied.

        Note: The current ``api`` instance is by default passed into the view. This is set in the dict ``api.jinja_values_base``.

        :param name_: The filename of the jinja2 template, in ``templates_dir``.
        :param values: Data to pass into the template.
        """
        # Prepopulate values with base
        values = {**self.jinja_values_base, **values}

        template = self.jinja_env.get_template(name_)
        return template.render(**values)

    def template_string(self, s_, **values):
        """Renders the given `jinja2 <http://jinja.pocoo.org/docs/>`_ template string, with provided values supplied.

        Note: The current ``api`` instance is by default passed into the view. This is set in the dict ``api.jinja_values_base``.

        :param s_: The template to use.
        :param values: Data to pass into the template.
        """
        # Prepopulate values with base
        values = {**self.jinja_values_base, **values}

        template = self.jinja_env.from_string(s_)
        return template.render(**values)

    def serve(self, *, address=None, port=None, debug=False, **options):
        """Runs the application with uvicorn. If the ``PORT`` environment
        variable is set, requests will be served on that port automatically to all
        known hosts.

        :param address: The address to bind to.
        :param port: The port to bind to. If none is provided, one will be selected at random.
        :param debug: Run uvicorn server in debug mode.
        :param options: Additional keyword arguments to send to ``uvicorn.run()``.
        """

        if "PORT" in os.environ:
            if address is None:
                address = "0.0.0.0"
            port = int(os.environ["PORT"])

        if address is None:
            address = "127.0.0.1"
        if port is None:
            port = 5042

        def spawn():
            uvicorn.run(self, host=address, port=port, debug=debug, **options)

        spawn()

    def run(self, **kwargs):
        if "debug" not in kwargs:
            kwargs.update({"debug": self.debug})
        self.serve(**kwargs)
