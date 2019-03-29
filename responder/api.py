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
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.middleware.errors import ServerErrorMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.lifespan import LifespanMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.routing import Router, LifespanHandler
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient
from starlette.websockets import WebSocket
from whitenoise import WhiteNoise

from . import models, status_codes
from .background import BackgroundQueue
from .formats import get_formats
from .routes import Route
from .statics import (
    DEFAULT_API_THEME,
    DEFAULT_CORS_PARAMS,
    DEFAULT_SECRET_KEY,
    DEFAULT_SESSION_COOKIE,
)
from .templates import GRAPHIQL


# TODO: consider moving status codes here
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

        self.apps = {}
        self.routes = {}
        self.before_requests = {"http": [], "ws": []}
        self.docs_theme = DEFAULT_API_THEME
        self.docs_route = docs_route
        self.schemas = {}
        self.session_cookie = DEFAULT_SESSION_COOKIE

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
        self.app = self.dispatch
        self.add_middleware(GZipMiddleware)

        if self.hsts_enabled:
            self.add_middleware(HTTPSRedirectMiddleware)

        self.add_middleware(TrustedHostMiddleware, allowed_hosts=self.allowed_hosts)

        self.lifespan_handler = LifespanMiddleware(LifespanHandler)

        if self.cors:
            self.add_middleware(CORSMiddleware, **self.cors_params)
        self.add_middleware(ServerErrorMiddleware, debug=debug)

        # Jinja enviroment
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
    def _default_wsgi_app(environ, start_response):
        pass

    @staticmethod
    def _notfound_wsgi_app(environ, start_response):
        start_response("404 NOT FOUND", [("Content-Type", "text/plain")])
        return [b"Not Found."]

    def before_request(self, websocket=False):
        def decorator(f):
            if websocket:
                self.before_requests.setdefault("ws", []).append(f)
            else:
                self.before_requests.setdefault("http", []).append(f)
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

        for route in self.routes:
            if self.routes[route].description:
                operations = yaml_utils.load_operations_from_docstring(
                    self.routes[route].description
                )
                spec.path(path=route, operations=operations)

        for name, schema in self.schemas.items():
            spec.components.schema(name, schema=schema)

        return spec

    @property
    def openapi(self):
        return self._apispec.to_yaml()

    def add_middleware(self, middleware_cls, **middleware_config):
        self.app = middleware_cls(self.app, **middleware_config)

    def __call__(self, scope):

        if scope["type"] == "lifespan":
            return self.lifespan_handler(scope)

        path = scope["path"]
        root_path = scope.get("root_path", "")

        # Call into a submounted app, if one exists.
        for path_prefix, app in self.apps.items():
            if path.startswith(path_prefix):
                scope["path"] = path[len(path_prefix) :]
                scope["root_path"] = root_path + path_prefix
                try:
                    return app(scope)
                except TypeError:
                    app = WSGIMiddleware(app)
                    return app(scope)

        return self.app(scope)

    def dispatch(self, scope):
        # Call the main dispatcher.
        async def asgi(receive, send):
            nonlocal scope, self

            if scope["type"] == "lifespan":
                return self.lifespan_handler(scope)
            elif scope["type"] == "websocket":
                await self._dispatch_ws(scope=scope, receive=receive, send=send)
            else:
                req = models.Request(scope, receive=receive, api=self)
                resp = await self._dispatch_request(
                    req, scope=scope, send=send, receive=receive
                )
                await resp(receive, send)

        return asgi

    async def _dispatch_ws(self, scope, receive, send):
        ws = WebSocket(scope=scope, receive=receive, send=send)

        route = self.path_matches_route(ws.url.path)
        route = self.routes.get(route)

        if route:
            for before_request in self.before_ws_requests:
                await self.background(before_request, ws=ws)
            await self.background(route.endpoint, ws)
        else:
            await send({"type": "websocket.close", "code": 1000})

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
        for (route, route_object) in self.routes.items():
            if route_object.does_match(path):
                return route

    @property
    def _signer(self):
        return itsdangerous.Signer(self.secret_key)

    def _prepare_session(self, resp):

        if resp.session:
            data = self._signer.sign(
                b64encode(json.dumps(resp.session).encode("utf-8"))
            )
            resp.cookies[self.session_cookie] = data.decode("utf-8")

    @staticmethod
    def no_response(req, resp, **params):
        pass

    async def _dispatch_request(self, req, **options):
        # Set formats on Request object.
        req.formats = self.formats

        # Get the route.
        route = self.path_matches_route(req.url.path)
        route = self.routes.get(route)
        if route:
            resp = models.Response(req=req, formats=self.formats)

            for before_request in self.before_http_requests:
                await self.background(before_request, req=req, resp=resp)

            await self._execute_route(route=route, req=req, resp=resp, **options)
        else:
            resp = models.Response(req=req, formats=self.formats)
            self.default_response(req=req, resp=resp, notfound=True)
        self.default_response(req=req, resp=resp)

        self._prepare_session(resp)

        return resp

    async def _execute_route(self, *, route, req, resp, **options):

        params = route.incoming_matches(req.url.path)

        cont = True

        if route.is_function:
            try:
                try:
                    # Run the view.
                    r = self.background(route.endpoint, req, resp, **params)
                    # If it's async, await it.
                    if hasattr(r, "cr_running"):
                        await r
                except TypeError as e:
                    cont = True
            except Exception:
                await self.background(self.default_response, req, resp, error=True)
                raise

        if route.is_class_based or cont:
            try:
                view = route.endpoint(**params)
            except TypeError:
                try:
                    view = route.endpoint()
                except TypeError:
                    view = route.endpoint
                    pass

            # Run on_request first.
            try:
                # Run the view.
                r = getattr(view, "on_request", self.no_response)
                r = self.background(r, req, resp, **params)
                # If it's async, await it.
                if hasattr(r, "send"):
                    await r
            except Exception:
                await self.background(self.default_response, req, resp, error=True)
                raise

            # Then run on_method.
            method = req.method
            try:
                # Run the view.
                r = getattr(view, f"on_{method}", self.no_response)
                r = self.background(r, req, resp, **params)
                # If it's async, await it.
                if hasattr(r, "send"):
                    await r
            except Exception:
                await self.background(self.default_response, req, resp, error=True)
                raise

    def add_event_handler(self, event_type, handler):
        """Adds an event handler to the API.

        :param event_type: A string in ("startup", "shutdown")
        :param handler: The function to run. Can be either a function or a coroutine.
        """

        self.lifespan_handler.add_event_handler(event_type, handler)

    def add_route(
        self,
        route=None,
        endpoint=None,
        *,
        default=False,
        static=False,
        check_existing=True,
        websocket=False,
        before_request=False,
    ):
        """Adds a route to the API.

        :param route: A string representation of the route.
        :param endpoint: The endpoint for the route -- can be a callable, or a class.
        :param default: If ``True``, all unknown requests will route to this view.
        :param static: If ``True``, and no endpoint was passed, render "static/index.html", and it will become a default route.
        :param check_existing: If ``True``, an AssertionError will be raised, if the route is already defined.
        """
        if before_request:
            if websocket:
                self.before_requests.setdefault("ws", []).append(endpoint)
            else:
                self.before_requests.setdefault("http", []).append(endpoint)
            return

        if route is None:
            route = f"/{uuid4().hex}"

        if check_existing:
            assert route not in self.routes

        if static:
            assert self.static_dir is not None
            if not endpoint:
                endpoint = self.static_response
                default = True

        if default:
            self.default_endpoint = endpoint

        self.routes[route] = Route(route, endpoint, websocket=websocket)
        # TODO: A better data structure or sort it once the app is loaded
        self.routes = dict(
            sorted(self.routes.items(), key=lambda item: item[1]._weight())
        )

    def default_response(
        self, req=None, resp=None, websocket=False, notfound=False, error=False
    ):
        if websocket:
            return

        if resp.status_code is None:
            resp.status_code = 200

        if self.default_endpoint and notfound:
            self.default_endpoint(req=req, resp=resp)
        else:
            if notfound:
                resp.status_code = status_codes.HTTP_404
                resp.text = "Not found."
            if error:
                resp.status_code = status_codes.HTTP_500
                resp.text = "Application error."

    def docs_response(self, req, resp):
        resp.html = self.docs

    def static_response(self, req, resp):

        assert self.static_dir is not None

        index = (self.static_dir / "index.html").resolve()
        if os.path.exists(index):
            with open(index, "r") as f:
                resp.html = f.read()
        else:
            resp.status_code = status_codes.HTTP_404
            resp.text = "Not found."

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

        # assert resp.status_code.is_300(status_code)

        resp.status_code = status_code
        if set_text:
            resp.text = f"Redirecting to: {location}"
        resp.headers.update({"Location": location})

    def on_event(self, event_type: str, **args):
        """Decorator for registering functions or coroutines to run at certain events
        Supported events: startup, cleanup, shutdown, tick

        Usage::

            @api.on_event('startup')
            async def open_database_connection_pool():
                ...

            @api.on_event('tick', seconds=10)
            async def do_stuff():
                ...

            @api.on_event('cleanup')
            async def close_database_connection_pool():
                ...

        """

        def decorator(func):
            self.add_event_handler(event_type, func, **args)
            return func

        return decorator

    def route(self, route=None, **options):
        """Decorator for creating new routes around function and class definitions.

        Usage::

            @api.route("/hello")
            def hello(req, resp):
                resp.text = "hello, world!"

        """

        def decorator(f):
            self.add_route(route, f, **options)
            return f

        return decorator

    def mount(self, route, app):
        """Mounts an WSGI / ASGI application at a given route.

        :param route: String representation of the route to be used (shouldn't be parameterized).
        :param app: The other WSGI / ASGI app.
        """
        self.apps.update({route: app})

    def session(self, base_url="http://;"):
        """Testing HTTP client. Returns a Requests session object, able to send HTTP requests to the Responder application.

        :param base_url: The URL to mount the connection adaptor to.
        """

        if self._session is None:
            self._session = TestClient(self, base_url=base_url)
        return self._session

    def _route_for(self, endpoint):
        for route_object in self.routes.values():
            if endpoint in (route_object.endpoint, route_object.endpoint_name):
                return route_object

    def url_for(self, endpoint, **params):
        # TODO: Absolute_url
        """Given an endpoint, returns a rendered URL for its route.

        :param endpoint: The route endpoint you're searching for.
        :param params: Data to pass into the URL generator (for parameterized URLs).
        """
        route_object = self._route_for(endpoint)
        if route_object:
            return route_object.url(**params)
        raise ValueError

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

        def static_url(asset):
            assert None not in (self.static_dir, self.static_route)
            return f"{self.static_route}/{asset}"

        return template.render(
            document=document,
            langs=["javascript", "python"],
            code_style=None,
            static_url=static_url,
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
