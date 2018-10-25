import os
import json
from functools import partial
from pathlib import Path

import uvicorn
import apistar
import yaml
import jinja2
import itsdangerous
from graphql_server import encode_execution_results, json_encode, default_format_error
from starlette.websockets import WebSocket
from starlette.debug import DebugMiddleware
from starlette.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from apispec import yaml_utils
from asgiref.wsgi import WsgiToAsgi
from whitenoise import WhiteNoise

from . import models
from . import status_codes
from .routes import Route
from .formats import get_formats
from .background import BackgroundQueue
from .templates import GRAPHIQL

# TODO: consider moving status codes here
class API:
    """The primary web-service class.

        :param static_dir: The directory to use for static files. Will be created for you if it doesn't already exist.
        :param templates_dir: The directory to use for templates. Will be created for you if it doesn't already exist.
        :param auto_escape: If ``True``, HTML and XML templates will automatically be escaped.
        :param enable_hsts: If ``True``, send all responses to HTTPS URLs.
    """

    status_codes = status_codes

    def __init__(
        self,
        *,
        debug=False,
        title=None,
        version=None,
        openapi=None,
        openapi_route="/schema.yml",
        static_dir="static",
        static_route="/static",
        templates_dir="templates",
        auto_escape=True,
        secret_key="NOTASECRET",
        enable_hsts=False,
        docs_route=None,
    ):
        self.secret_key = secret_key
        self.title = title
        self.version = version
        self.openapi_version = openapi
        self.static_dir = Path(os.path.abspath(static_dir))
        self.static_route = static_route
        self.templates_dir = Path(os.path.abspath(templates_dir))
        self.built_in_templates_dir = Path(
            os.path.abspath(os.path.dirname(__file__) + "/templates")
        )
        self.routes = {}
        self.docs_theme = "swaggerui"
        self.docs_route = docs_route
        self.schemas = {}
        self.session_cookie = "Responder-Session"

        self.hsts_enabled = enable_hsts

        # Make the static/templates directory if they don't exist.
        for _dir in (self.static_dir, self.templates_dir):
            os.makedirs(_dir, exist_ok=True)

        self.whitenoise = WhiteNoise(
            application=self._default_wsgi_app, index_file=True
        )
        self.whitenoise.add_files(str(self.static_dir))

        self.whitenoise.add_files(
            (
                Path(apistar.__file__).parent / "themes" / self.docs_theme / "static"
            ).resolve()
        )

        self.apps = {}
        self.mount(self.static_route, self.whitenoise)

        self.formats = get_formats()

        # Cached requests session.
        self._session = None
        self.background = BackgroundQueue()

        if self.openapi_version:
            self.add_route(openapi_route, self.schema_response)

        if self.docs_route:
            self.add_route(self.docs_route, self.docs_response)

        self.default_endpoint = None
        self.app = self.dispatch
        self.add_middleware(GZipMiddleware)
        if debug:
            self.add_middleware(DebugMiddleware)
        if self.hsts_enabled:
            self.add_middleware(HTTPSRedirectMiddleware)

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
    def _default_wsgi_app(*args, **kwargs):
        pass

    @property
    def _apispec(self):
        spec = APISpec(
            title=self.title,
            version=self.version,
            openapi_version=self.openapi_version,
            plugins=[MarshmallowPlugin()],
        )

        for route in self.routes:
            if self.routes[route].description:
                operations = yaml_utils.load_operations_from_docstring(
                    self.routes[route].description
                )
                spec.add_path(path=route, operations=operations)

        for name, schema in self.schemas.items():
            spec.definition(name, schema=schema)

        return spec

    @property
    def openapi(self):
        return self._apispec.to_yaml()

    def add_middleware(self, middleware_cls, **middleware_config):
        self.app = middleware_cls(self.app, **middleware_config)

    def __call__(self, scope):
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
                    app = WsgiToAsgi(app)
                    return app(scope)

        return self.app(scope)

    def dispatch(self, scope):
        # Call the main dispatcher.
        async def asgi(receive, send):
            nonlocal scope, self

            req = models.Request(scope, receive=receive, api=self)
            resp = await self._dispatch_request(
                req, scope=scope, send=send, receive=receive
            )
            await resp(receive, send)

        return asgi

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

    def _prepare_cookies(self, resp):
        if resp.cookies:
            header = " ".join([f"{k}={v}" for k, v in resp.cookies.items()])
            resp.headers["Set-Cookie"] = header

    @property
    def _signer(self):
        return itsdangerous.Signer(self.secret_key)

    def _prepare_session(self, resp):

        if resp.session:
            data = self._signer.sign(json.dumps(resp.session).encode("utf-8"))
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

        # Create the response object.
        cont = False
        if route:
            if route.uses_websocket:
                resp = WebSocket(**options)

            else:
                resp = models.Response(req=req, formats=self.formats)

            params = route.incoming_matches(req.url.path)

            if route.is_graphql:
                await self.graphql_response(req, resp, schema=route.endpoint)

            elif route.is_function:
                try:
                    try:
                        # Run the view.
                        r = route.endpoint(req, resp, **params)
                        # If it's async, await it.
                        if hasattr(r, "cr_running"):
                            await r
                    except TypeError as e:
                        cont = True
                except Exception:
                    self.default_response(req, resp, error=True)

            elif route.is_class_based or cont:
                try:
                    view = route.endpoint(**params)
                except TypeError:
                    view = route.endpoint()

                # Run on_request first.
                try:
                    # Run the view.
                    r = getattr(view, "on_request", self.no_response)(
                        req, resp, **params
                    )
                    # If it's async, await it.
                    if hasattr(r, "send"):
                        await r
                except Exception as e:
                    self.default_response(req, resp, error=True)

                # Then on_get.
                method = req.method

                # Run on_request first.
                try:
                    # Run the view.
                    r = getattr(view, f"on_{method}", self.no_response)(
                        req, resp, **params
                    )
                    # If it's async, await it.
                    if hasattr(r, "send"):
                        await r
                except Exception as e:

                    self.default_response(req, resp, error=True)

        else:
            resp = models.Response(req=req, formats=self.formats)
            self.default_response(req, resp, notfound=True)
        self.default_response(req, resp)

        self._prepare_session(resp)
        self._prepare_cookies(resp)

        return resp

    def add_route(
        self,
        route,
        endpoint=None,
        *,
        default=False,
        static=False,
        check_existing=True,
        websocket=False,
    ):
        """Add a route to the API.

        :param route: A string representation of the route.
        :param endpoint: The endpoint for the route -- can be a callable, a class, or graphene schema (GraphQL).
        :param default: If ``True``, all unknown requests will route to this view.
        :param static: If ``True``, and no endpoint was passed, render "static/index.html", and it will become a default route.
        :param check_existing: If ``True``, an AssertionError will be raised, if the route is already defined.
        """
        if check_existing:
            assert route not in self.routes

        if not endpoint and static:
            endpoint = self.static_response
            default = True

        if default:
            self.default_endpoint = endpoint

        # Can we remove it ?
        try:
            if callable(endpoint):
                endpoint.is_routed = True
        except AttributeError:
            pass

        self.routes[route] = Route(route, endpoint, websocket=websocket)
        # TODO: A better data structure or sort it once the app is loaded
        self.routes = dict(
            sorted(self.routes.items(), key=lambda item: item[1]._weight())
        )

    def default_response(self, req, resp, notfound=False, error=False):
        if resp.status_code is None:
            resp.status_code = 200

        if self.default_endpoint and notfound:
            self.default_endpoint(req, resp)
        else:
            if notfound:
                resp.status_code = status_codes.HTTP_404
                resp.text = "Not found."
            if error:
                resp.status_code = status_codes.HTTP_500
                resp.text = "Application error."

    def docs_response(self, req, resp):
        resp.text = self.docs

    def static_response(self, req, resp):
        index = (self.static_dir / "index.html").resolve()
        resp.content = ""
        if os.path.exists(index):
            with open(index, "r") as f:
                resp.text = f.read()

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

        assert resp.status_code.is_300(status_code)

        resp.status_code = status_code
        if set_text:
            resp.text = f"Redirecting to: {location}"
        resp.headers.update({"Location": location})

    @staticmethod
    async def _resolve_graphql_query(req):
        # TODO: Get variables and operation_name from form data, params, request text?

        if "json" in req.mimetype:
            json_media = await req.media("json")
            return (
                json_media["query"],
                json_media.get("variables"),
                json_media.get("operationName"),
            )

        # Support query/q in form data.
        # Form data is awaiting https://github.com/encode/starlette/pull/102
        # if "query" in req.media("form"):
        #     return req.media("form")["query"], None, None
        # if "q" in req.media("form"):
        #     return req.media("form")["q"], None, None

        # Support query/q in params.
        if "query" in req.params:
            return req.params["query"], None, None
        if "q" in req.params:
            return req.params["q"], None, None

        # Otherwise, the request text is used (typical).
        # TODO: Make some assertions about content-type here.
        return req.text, None, None

    async def graphql_response(self, req, resp, schema):
        show_graphiql = req.method == "get" and req.accepts("text/html")

        if show_graphiql:
            resp.content = self.template_string(GRAPHIQL, endpoint=req.url.path)
            return

        query, variables, operation_name = await self._resolve_graphql_query(req)
        result = schema.execute(
            query, variables=variables, operation_name=operation_name
        )
        result, status_code = encode_execution_results(
            [result],
            is_batch=False,
            format_error=default_format_error,
            encode=partial(json_encode, pretty=False),
        )
        resp.media = json.loads(result)
        return (query, result, status_code)

    def route(self, route, **options):
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
            self._session = TestClient(self)
        return self._session

    def _route_for(self, endpoint):
        for (route, route_object) in self.routes.items():
            if route_object.endpoint == endpoint:
                return route_object
            elif route_object.endpoint_name == endpoint:
                return route_object

    def url_for(self, endpoint, **params):
        # TODO: Absolute_url
        """Given an endpoint, returns a rendered URL for its route.

        :param view: The route endpoint you're searching for.
        :param params: Data to pass into the URL generator (for parameterized URLs).
        """
        route_object = self._route_for(endpoint)
        if route_object:
            return route_object.url(**params)
        raise ValueError

    def static_url(self, asset):
        """Given a static asset, return its URL path."""
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
            return f"{self.static_route}/{asset}"
            # return asset

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

    def run(self, address=None, port=None, debug=False, **options):
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

        uvicorn.run(self, host=address, port=port, debug=debug, **options)
