import os
from pathlib import Path

import graphene

from whitenoise import WhiteNoise
from wsgiadapter import WSGIAdapter as RequestsWSGIAdapter
from requests import Session as RequestsSession

from . import models
from .status import HTTP_404


class BaseAPI:
    __slots__ = ["routes"]

    def __init__(self):
        self.routes = {}

    def _wsgi_app(self, environ, start_response):
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

        req = models.Request.from_environ(environ)
        resp = self._dispatch_request(req)

        return resp(environ, start_response)

    def wsgi_app(self, environ, start_response):
        return self.whitenoise(environ, start_response)

    def __call__(self, environ, start_response):
        """The WSGI server calls the Flask application object as the
        WSGI application. This calls :meth:`wsgi_app` which can be
        wrapped to applying middleware."""
        return self.wsgi_app(environ, start_response)

    def path_matches_route(self, url):
        for (route, view) in self.routes.items():
            if url == route:
                return route

    def _dispatch_request(self, req):
        route = self.path_matches_route(req.path)
        resp = models.Response(req=req)

        if route:
            try:
                self.routes[route](req, resp)
            # The request is using class-based views.
            except TypeError:
                try:
                    view = self.routes[route]()
                # GraphQL Schema.
                except TypeError:
                    view = self.routes[route]
                    self.graphql_response(req, resp, schema=view)

                # Run on_request first.
                try:
                    getattr(view, "on_request")(req, resp)
                except AttributeError:
                    pass

                # Then on_get.
                method = req.method.lower()

                try:
                    getattr(view, f"on_{method}")(req, resp)
                except AttributeError:
                    pass
        else:
            self.default_response(req, resp)

        return resp

    @property
    def static_dir(self):
        return Path(".")


class API(BaseAPI):
    __slots__ = ("routes", "_session", "whitenoise", "static_dir")

    def __init__(self, static="static"):
        super().__init__()
        self._session = None
        self.static_dir = Path(os.path.abspath(static))

        # Make the static directory if it doesn't exist.
        os.makedirs(self.static_dir, exist_ok=True)

        # Mount the whitenoise application.
        self.whitenoise = WhiteNoise(self._wsgi_app, root=str(self.static_dir))

    def add_route(self, route, view, *, check_existing=True, graphiql=False):
        if check_existing:
            assert route not in self.routes

        # TODO: Support grpahiql.

        self.routes[route] = view

    def default_response(self, req, resp):
        resp.status_code = HTTP_404
        resp.text = "Not found."

    @staticmethod
    def _resolve_graphql_query(req):
        # Support query/q in form data.
        if "query" in req.data:
            return req.data["query"]
        if "q" in req.data:
            return req.data["q"]

        # Support query/q in params.
        if "query" in req.params:
            return req.params["query"][0]
        if "q" in req.params:
            return req.params["q"][0]

        # Otherwise, the request text is used (typical).
        # TODO: Make some assertions about content-type here.
        return req.text

    def graphql_response(self, req, resp, schema):
        query = self._resolve_graphql_query(req)
        result = schema.execute(query)
        resp.media = dict(result.data)

    def route(self, route, **options):
        def decorator(f):
            self.add_route(route, f)
            return f

        return decorator

    def session(self, base_url="http://app"):
        if self._session is None:
            session = RequestsSession()
            session.mount(base_url, RequestsWSGIAdapter(self))
            self._session = session
        return self._session
