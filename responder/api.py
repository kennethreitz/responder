import os
import json
from functools import partial
from pathlib import Path

import waitress

import jinja2
from whitenoise import WhiteNoise
from wsgiadapter import WSGIAdapter as RequestsWSGIAdapter
from requests import Session as RequestsSession
from werkzeug.wsgi import DispatcherMiddleware
from graphql_server import encode_execution_results, json_encode, default_format_error

from . import models
from .status_codes import HTTP_404
from . import status_codes
from .routes import Route
from .formats import get_formats


# TODO: consider moving status codes here
class API:
    """The primary web-service class.

        :param static_dir: The directory to use for static files. Will be created for you if it doesn't already exist.
        :param templates_dir: The directory to use for templates. Will be created for you if it doesn't already exist.
        :param enable_hsts: If ``True``, send all responses to HTTPS URLs.
    """

    status_codes = status_codes

    def __init__(
        self, static_dir="static", templates_dir="templates", enable_hsts=False
    ):
        self.static_dir = Path(os.path.abspath(static_dir))
        self.templates_dir = Path(os.path.abspath(templates_dir))
        self.routes = {}
        self.hsts_enabled = enable_hsts
        self.apps = {"/": self._wsgi_app}
        self.formats = get_formats()

        # Make the static/templates directory if they don't exist.
        for _dir in (self.static_dir, self.templates_dir):
            os.makedirs(_dir, exist_ok=True)

        # Mount the whitenoise application.
        self.whitenoise = WhiteNoise(self.__wsgi_app, root=str(self.static_dir))

        # Cached requests session.
        self._session = None

    def __wsgi_app(self, environ, start_response):
        # def wsgi_app(self, request):
        """The actual WSGI application. This is not implemented in
        :meth:`__call__` so that middlewares can be applied without
        losing a reference to the app object. Instead of doing this::

            app = MyMiddleware(app)

        It's a better idea to do this instead::

            app.wsgi_app = MyMiddleware(app.wsgi_app)

        Then you still have the original application object around and
        can continue to call methods on it.

        .. versionchanged:: 0.7
            Teardown events for the request and app contexts are called
            even if an unhandled error occurs. Other events may not be
            called depending on when an error occurs during dispatch.
            See :ref:`callbacks-and-errors`.

        :param environ: A WSGI environment.
        :param start_response: A callable accepting a status code,
            a list of headers, and an optional exception context to
            start the response.
        """

        req = models.Request(environ, start_response)
        # if not req.dispatched:
        resp = self._dispatch_request(req)
        return resp(environ, start_response)

    def _wsgi_app(self, environ, start_response):
        return self.whitenoise(environ, start_response)

    def wsgi_app(self, environ, start_response):
        """Returns the WSGI app for this application (including all mounted WSGI apps)."""
        apps = self.apps.copy()
        main = apps.pop("/")

        return DispatcherMiddleware(main, apps)(environ, start_response)

    def __call__(self, environ, start_response=None):
        """The WSGI server calls the Flask application object as the
        WSGI application. This calls :meth:`wsgi_app` which can be
        wrapped to applying middleware."""
        return self.wsgi_app(environ, start_response)

    def path_matches_route(self, path):
        """Given a path portion of a URL, tests that it matches against any registered route.

        :param path: The path portion of a URL, to test all known routes against.
        """
        for (route, route_object) in self.routes.items():
            if route_object.does_match(path):
                return route

    def _dispatch_request(self, req):
        # Set formats on Request object.
        req.formats = self.formats

        route = self.path_matches_route(req.path)
        resp = models.Response(req=req, formats=self.formats)

        if self.hsts_enabled:
            if req.url.startswith("http://"):
                url = req.url.replace("http://", "https://", 1)
                self.redirect(resp, location=url)

        if route:
            try:
                params = self.routes[route].incoming_matches(req.path)
                self.routes[route].endpoint(req, resp, **params)
            # The request is using class-based views.
            except TypeError:
                try:
                    view = self.routes[route].endpoint(**params)
                except TypeError:
                    view = self.routes[route].endpoint
                    try:
                        # GraphQL Schema.
                        assert hasattr(view, "execute")
                        self.graphql_response(req, resp, schema=view)
                    except AssertionError:
                        # WSGI App.
                        try:
                            req.dispatched = True
                            return view(
                                environ=req._environ, start_response=req._start_response
                            )
                        except TypeError:
                            pass

                # Run on_request first.
                try:
                    getattr(view, "on_request")(req, resp)
                except AttributeError:
                    pass

                # Then on_get.
                method = req.method

                try:
                    getattr(view, f"on_{method}")(req, resp)
                except AttributeError:
                    pass
        else:
            self.default_response(req, resp)

        return resp

    def add_route(self, route, endpoint, *, check_existing=True):
        # TODO: add graphiql
        """Add a route to the API.

        :param route: A string representation of the route.
        :param endpoint: The endpoint for the route -- can be a callable, a class, a WSGI application, or graphene schema (GraphQL).
        :param check_existing: If ``True``, an AssertionError will be raised, if the route is already defined.
        """
        if check_existing:
            assert route not in self.routes

        # TODO: Support grpahiql.
        self.routes[route] = Route(route, endpoint)

    def default_response(self, req, resp):
        resp.status_code = HTTP_404
        resp.text = "Not found."

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
    def _resolve_graphql_query(req):
        if "json" in req.mimetype:
            return req.json()["query"]

        # Support query/q in form data.
        if "query" in req.media("form"):
            return req.media("form")["query"]
        if "q" in req.media("form"):
            return req.media("form")["q"]

        # Support query/q in params.
        if "query" in req.params:
            return req.params["query"]
        if "q" in req.params:
            return req.params["q"]

        # Otherwise, the request text is used (typical).
        # TODO: Make some assertions about content-type here.
        return req.text

    def graphql_response(self, req, resp, schema):
        query = self._resolve_graphql_query(req)
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
        """Decorator for creating new routes around function and class defenitions.

        Usage::

            @api.route("/hello")
            def hello(req, resp):
                req.text = "hello, world!"

        """

        def decorator(f):
            self.add_route(route, f, **options)
            return f

        return decorator

    def mount(self, route, wsgi_app):
        """Mounts a WSGI application at a given route.

        :param route: String representation of the route to be used (shouldn't be parameterized).
        :param wsgi_app: The other WSGI app (e.g. a Flask app).
        """
        self.apps.update({route: wsgi_app})

    def session(self, base_url="http://;"):
        """Testing HTTP client. Returns a Requests session object, able to send HTTP requests to the WSGI application.

        :param base_url: The URL to mount the connection adaptor to.
        """

        if self._session is None:
            session = RequestsSession()
            session.mount(base_url, RequestsWSGIAdapter(self))
            self._session = session
        return self._session

    def url_for(self, endpoint, absolute_url=False, **params):
        # TODO: Absolute_url
        """Given an endpoint, returns a rendered URL for its route.

        :param view: The route endpoint you're searching for.
        :param params: Data to pass into the URL generator (for parameterized URLs).
        """
        for (route, route_object) in self.routes.items():
            if route_object.endpoint == endpoint:
                return route_object.url(**params)
        raise ValueError

    def template(self, name, auto_escape=True, **values):
        """Renders the given `jinja2 <http://jinja.pocoo.org/docs/>`_ template, with provided values supplied.

        Note: The current ``api`` instance is always passed into the view.

        :param name: The filename of the jinja2 template, in ``templates_dir``.
        :param auto_escape: If ``True``, HTML and XML will automatically be escaped.
        :param values: Data to pass into the template.
        """
        # Give reference to self.
        values.update(api=self)

        if auto_escape:
            env = jinja2.Environment(
                loader=jinja2.FileSystemLoader(
                    str(self.templates_dir), followlinks=True
                ),
                autoescape=jinja2.select_autoescape(["html", "xml"]),
            )
        else:
            env = jinja2.Environment(
                loader=jinja2.FileSystemLoader(
                    str(self.templates_dir), followlinks=True
                ),
                autoescape=jinja2.select_autoescape([]),
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

    def run(self, address=None, port=None, **kwargs):
        """Runs the application with Waitress. If the ``PORT`` environment
        variable is set, requests will be served on that port automatically to all
        known hosts.

        :param address: The address to bind to.
        :param port: The port to bind to. If none is provided, one will be selected at random.
        :param kwargs: Additional keyword arguments to send to ``waitress.serve()``.
        """
        if "PORT" in os.environ:
            if address is None:
                address = "0.0.0.0"
            port = os.environ["PORT"]

        if address is None:
            address = "127.0.0.1"
        if port is None:
            port = 0

        bind_to = f"{address}:{port}"

        waitress.serve(app=self, listen=bind_to, **kwargs)
