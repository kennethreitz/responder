import os
import json
from functools import partial
from pathlib import Path

import uvicorn

import asyncio
import jinja2
from graphql_server import encode_execution_results, json_encode, default_format_error
from starlette.routing import Router
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient
from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from apispec import yaml_utils
from asgiref.wsgi import WsgiToAsgi

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
        :param enable_hsts: If ``True``, send all responses to HTTPS URLs.
    """

    status_codes = status_codes

    def __init__(
        self,
        *,
        title=None,
        version=None,
        openapi=None,
        openapi_route="/schema.yml",
        static_dir="static",
        templates_dir="templates",
        enable_hsts=False,
    ):
        self.title = title
        self.version = version
        self.openapi_version = openapi
        self.static_dir = Path(os.path.abspath(static_dir))
        self.static_route = f"/{static_dir}"
        self.templates_dir = Path(os.path.abspath(templates_dir))
        self.built_in_templates_dir = Path(
            os.path.abspath(os.path.dirname(__file__) + "/templates")
        )
        self.routes = {}
        self.schemas = {}

        self.hsts_enabled = enable_hsts
        self.static_files = StaticFiles(directory=str(self.static_dir))
        self.apps = {self.static_route: self.static_files}

        self.formats = get_formats()

        # Make the static/templates directory if they don't exist.
        for _dir in (self.static_dir, self.templates_dir):
            os.makedirs(_dir, exist_ok=True)

        # Cached requests session.
        self._session = None
        self.background = BackgroundQueue()

        if self.openapi_version:
            self.add_route(openapi_route, self.schema_response)

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

        # Call the main dispatcher.
        async def asgi(receive, send):
            nonlocal scope, self

            req = models.Request(scope, receive=receive)
            resp = await self._dispatch_request(req)
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

    async def _dispatch_request(self, req):
        # Set formats on Request object.
        req.formats = self.formats

        route = self.path_matches_route(req.url.path)
        resp = models.Response(req=req, formats=self.formats)

        if self.hsts_enabled:
            if req.url.startswith("http://"):
                url = req.url.replace("http://", "https://", 1)
                self.redirect(resp, location=url)

        if route:
            try:
                params = self.routes[route].incoming_matches(req.url.path)
                result = self.routes[route].endpoint(req, resp, **params)
                if hasattr(result, "cr_running"):
                    await result
            # The request is using class-based views.
            except TypeError as e:
                try:
                    view = self.routes[route].endpoint(**params)
                except TypeError:
                    view = self.routes[route].endpoint

                    if self.routes[route].is_graphql:
                        await self.graphql_response(req, resp, schema=view)
                    else:
                        # WSGI App.
                        # try:
                        #     return view(
                        #         environ=req._environ, start_response=req._start_response
                        #     )
                        # except TypeError:
                        #     pass
                        pass

                # Run on_request first.
                try:
                    r = getattr(view, "on_request")(req, resp)
                    if hasattr(r, 'send'):
                        await r
                except AttributeError:
                    pass

                # Then on_get.
                method = req.method

                try:
                    r = getattr(view, f"on_{method}")(req, resp)
                    if hasattr(r, 'send'):
                        await r
                except AttributeError:
                    pass
        else:
            self.default_response(req, resp)

        return resp

    def add_route(self, route, endpoint, *, check_existing=True):
        """Add a route to the API.

        :param route: A string representation of the route.
        :param endpoint: The endpoint for the route -- can be a callable, a class, a WSGI application, or graphene schema (GraphQL).
        :param check_existing: If ``True``, an AssertionError will be raised, if the route is already defined.
        """
        if check_existing:
            assert route not in self.routes
        self.routes[route] = Route(route, endpoint)
        # TODO: A better datastructer or sort it once the app is loaded
        self.routes = dict(
            sorted(self.routes.items(), key=lambda item: item[1]._weight())
        )

    def default_response(self, req, resp):
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

        assert resp.status_code.is_300(status_code)

        resp.status_code = status_code
        if set_text:
            resp.text = f"Redirecting to: {location}"
        resp.headers.update({"Location": location})

    @staticmethod
    async def _resolve_graphql_query(req):
        if "json" in req.mimetype:
            return (await req.media("json"))["query"]

        # Support query/q in form data.
        # Form data is awaiting https://github.com/encode/starlette/pull/102
        # if "query" in req.media("form"):
        #     return req.media("form")["query"]
        # if "q" in req.media("form"):
        #     return req.media("form")["q"]

        # Support query/q in params.
        if "query" in req.params:
            return req.params["query"]
        if "q" in req.params:
            return req.params["q"]

        # Otherwise, the request text is used (typical).
        # TODO: Make some assertions about content-type here.
        return req.text

    async def graphql_response(self, req, resp, schema):
        show_graphiql = req.method == "get" and req.accepts("text/html")

        if show_graphiql:
            resp.content = self.template_string(GRAPHIQL, endpoint=req.url.path)
            return

        query = await self._resolve_graphql_query(req)
        result = schema.execute(query)
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

    def url_for(self, endpoint, testing=False, **params):
        # TODO: Absolute_url
        """Given an endpoint, returns a rendered URL for its route.

        :param view: The route endpoint you're searching for.
        :param params: Data to pass into the URL generator (for parameterized URLs).
        """
        for (route, route_object) in self.routes.items():
            if route_object.endpoint == endpoint:
                return route_object.url(testing=testing, **params)
        raise ValueError

    def static_url(self, asset):
        """Given a static asset, return its URL path."""
        return f"{self.static_route}/{str(asset)}"

    def template(self, name, auto_escape=True, **values):
        """Renders the given `jinja2 <http://jinja.pocoo.org/docs/>`_ template, with provided values supplied.

        Note: The current ``api`` instance is always passed into the view.

        :param name: The filename of the jinja2 template, in ``templates_dir``.
        :param auto_escape: If ``True``, HTML and XML will automatically be escaped.
        :param values: Data to pass into the template.
        """
        # Give reference to self.
        values.update(api=self)

        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(
                [str(self.templates_dir), str(self.built_in_templates_dir)],
                followlinks=True,
            ),
            autoescape=jinja2.select_autoescape(["html", "xml"] if auto_escape else []),
        )

        template = env.get_template(name)
        return template.render(**values)

    def template_string(self, s, auto_escape=True, **values):
        """Renders the given `jinja2 <http://jinja.pocoo.org/docs/>`_ template string, with provided values supplied.

        Note: The current ``api`` instance is always passed into the view.

        :param s: The template to use.
        :param auto_escape: If ``True``, HTML and XML will automatically be escaped.
        :param values: Data to pass into the template.
        """
        # Give reference to self.
        values.update(api=self)

        if auto_escape:
            env = jinja2.Environment(
                loader=jinja2.BaseLoader,
                autoescape=jinja2.select_autoescape(["html", "xml"]),
            )
        else:
            env = jinja2.Environment(
                loader=jinja2.BaseLoader, autoescape=jinja2.select_autoescape([])
            )

        template = env.from_string(s)
        return template.render(**values)

    def run(self, address=None, port=None, **options):
        """Runs the application with uvicorn. If the ``PORT`` environment
        variable is set, requests will be served on that port automatically to all
        known hosts.

        :param address: The address to bind to.
        :param port: The port to bind to. If none is provided, one will be selected at random.
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

        uvicorn.run(self, host=address, port=port, **options)
