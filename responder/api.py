import json
import os
from functools import partial
from pathlib import Path

import jinja2
import waitress
from graphql_server import default_format_error, encode_execution_results, json_encode
from requests import Session as RequestsSession
from werkzeug.wsgi import DispatcherMiddleware
from whitenoise import WhiteNoise
from wsgiadapter import WSGIAdapter as RequestsWSGIAdapter

from . import models, status_codes
from .routes import Route
from .status_codes import HTTP_404


# TODO: consider moving status codes here
class API:
    status_codes = status_codes

    def __init__(
        self, static_dir="static", templates_dir="templates", enable_hsts=False
    ):
        self.static_dir = Path(os.path.abspath(static_dir))
        self.templates_dir = Path(os.path.abspath(templates_dir))
        self.routes = {}
        self.hsts_enabled = enable_hsts
        self.apps = {"/": self._wsgi_app}

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

        req = models.Request.from_environ(environ, start_response)
        # if not req.dispatched:
        resp = self._dispatch_request(req)
        return resp(environ, start_response)

    def _wsgi_app(self, environ, start_response):
        return self.whitenoise(environ, start_response)

    def wsgi_app(self, environ, start_response):
        apps = self.apps.copy()
        main = apps.pop("/")

        return DispatcherMiddleware(main, apps)(environ, start_response)

    def __call__(self, environ, start_response=None):
        """The WSGI server calls the Flask application object as the
        WSGI application. This calls :meth:`wsgi_app` which can be
        wrapped to applying middleware."""
        return self.wsgi_app(environ, start_response)

    def path_matches_route(self, url):
        for (route, route_object) in self.routes.items():
            if route_object.does_match(url):
                return route

    def _dispatch_request(self, req):
        route = self.path_matches_route(req.path)
        resp = models.Response(req=req)

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

    def add_route(self, route, view, *, check_existing=True, graphiql=False):
        if check_existing:
            assert route not in self.routes

        # TODO: Support grpahiql.
        self.routes[route] = Route(route, view)

    def default_response(self, req, resp):
        resp.status_code = HTTP_404
        resp.text = "Not found."

    def redirect(self, resp, location, *, status_code=status_codes.HTTP_301):
        resp.status_code = status_code
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
        def decorator(f):
            self.add_route(route, f, **options)
            return f

        return decorator

    def mount(self, route, wsgi_app):
        self.apps.update({route: wsgi_app})

    def session(self, base_url="http://;"):
        if self._session is None:
            session = RequestsSession()
            session.mount(base_url, RequestsWSGIAdapter(self))
            self._session = session
        return self._session

    def url_for(self, view, absolute_url=False, **params):
        for (route, route_object) in self.routes.items():
            if route_object.endpoint == _view:
                return route_object.url(**params)
        raise ValueError

    def template(self, name, auto_escape=True, **values):
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
