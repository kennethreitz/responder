import asyncio
import json
import re
import inspect
import traceback
from collections import defaultdict

from starlette.middleware.wsgi import WSGIMiddleware
from starlette.websockets import WebSocket, WebSocketClose
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from .models import Request, Response
from . import status_codes
from .formats import get_formats
from .statics import DEFAULT_SESSION_COOKIE


_CONVERTORS = {
    "int": (int, r"\d+"),
    "str": (str, r"[^/]+"),
    "float": (float, r"\d+(.\d+)?"),
}

PARAM_RE = re.compile("{([a-zA-Z_][a-zA-Z0-9_]*)(:[a-zA-Z_][a-zA-Z0-9_]*)?}")


def compile_path(path):
    path_re = "^"
    param_convertors = {}
    idx = 0

    for match in PARAM_RE.finditer(path):
        param_name, convertor_type = match.groups(default="str")
        convertor_type = convertor_type.lstrip(":")
        assert (
            convertor_type in _CONVERTORS.keys()
        ), f"Unknown path convertor '{convertor_type}'"
        convertor, convertor_re = _CONVERTORS[convertor_type]

        path_re += path[idx : match.start()]
        path_re += rf"(?P<{param_name}>{convertor_re})"

        param_convertors[param_name] = convertor

        idx = match.end()

    path_re += path[idx:] + "$"

    return re.compile(path_re), param_convertors


class BaseRoute:
    def matches(self, scope):
        raise NotImplementedError()

    async def __call__(self, scope, receive, send):
        raise NotImplementedError()


class Route(BaseRoute):
    def __init__(self, route, endpoint, *, before_request=False):
        assert route.startswith("/"), "Route path must start with '/'"
        self.route = route
        self.endpoint = endpoint
        self.before_request = before_request

        self.path_re, self.param_convertors = compile_path(route)

    def __repr__(self):
        return f"<Route {self.route!r}={self.endpoint!r}>"

    def url(self, **params):
        return self.route.format(**params)

    @property
    def endpoint_name(self):
        return self.endpoint.__name__

    @property
    def description(self):
        return self.endpoint.__doc__

    def matches(self, scope):
        if scope["type"] != "http":
            return False, {}

        path = scope["path"]
        match = self.path_re.match(path)

        if match is None:
            return False, {}

        matched_params = match.groupdict()
        for key, value in matched_params.items():
            matched_params[key] = self.param_convertors[key](value)

        return True, {"path_params": {**matched_params}}

    async def __call__(self, scope, receive, send):
        request = Request(scope, receive, formats=get_formats())
        response = Response(req=request, formats=get_formats())

        path_params = scope.get("path_params", {})
        before_requests = scope.get("before_requests", [])

        for before_request in before_requests.get("http", []):
            if asyncio.iscoroutinefunction(before_request):
                await before_request(request, response)
            else:
                await run_in_threadpool(before_request, request, response)

        views = []

        if inspect.isclass(self.endpoint):
            endpoint = self.endpoint()
            on_request = getattr(endpoint, "on_request", None)
            if on_request:
                views.append(on_request)

            method_name = f"on_{request.method}"
            try:
                view = getattr(endpoint, method_name)
                views.append(view)
            except AttributeError:
                if on_request is None:
                    raise HTTPException(status_code=status_codes.HTTP_405)
        else:
            views.append(self.endpoint)

        for view in views:
            # "Monckey patch" for graphql: explicitly checking __call__
            if asyncio.iscoroutinefunction(view) or asyncio.iscoroutinefunction(
                view.__call__
            ):
                await view(request, response, **path_params)
            else:
                await run_in_threadpool(view, request, response, **path_params)

        if response.status_code is None:
            response.status_code = status_codes.HTTP_200

        await response(scope, receive, send)

    def __eq__(self, other):
        # [TODO] compare to str ?
        return self.route == other.route and self.endpoint == other.endpoint

    def __hash__(self):
        return hash(self.route) ^ hash(self.endpoint) ^ hash(self.before_request)


class WebSocketRoute(BaseRoute):
    def __init__(self, route, endpoint, *, before_request=False):
        assert route.startswith("/"), "Route path must start with '/'"
        self.route = route
        self.endpoint = endpoint
        self.before_request = before_request

        self.path_re, self.param_convertors = compile_path(route)

    def __repr__(self):
        return f"<Route {self.route!r}={self.endpoint!r}>"

    def url(self, **params):
        return self.route.format(**params)

    @property
    def endpoint_name(self):
        return self.endpoint.__name__

    @property
    def description(self):
        return self.endpoint.__doc__

    def matches(self, scope):
        if scope["type"] != "websocket":
            return False, {}

        path = scope["path"]
        match = self.path_re.match(path)

        if match is None:
            return False, {}

        matched_params = match.groupdict()
        for key, value in matched_params.items():
            matched_params[key] = self.param_convertors[key](value)

        return True, {"path_params": {**matched_params}}

    async def __call__(self, scope, receive, send):
        ws = WebSocket(scope, receive, send)

        before_requests = scope.get("before_requests", [])
        for before_request in before_requests.get("ws", []):
            await before_request(ws)

        await self.endpoint(ws)

    def __eq__(self, other):
        # [TODO] compare to str ?
        return self.route == other.route and self.endpoint == other.endpoint

    def __hash__(self):
        return hash(self.route) ^ hash(self.endpoint) ^ hash(self.before_request)


class Router:
    def __init__(self, routes=None, default_response=None, before_requests=None):
        self.routes = [] if routes is None else list(routes)
        # [TODO] Make its own router
        self.apps = {}
        self.default_endpoint = (
            self.default_response if default_response is None else default_response
        )
        self.before_requests = (
            {"http": [], "ws": []} if before_requests is None else before_requests
        )
        self.events = defaultdict(list)

    def add_route(
        self,
        route=None,
        endpoint=None,
        *,
        default=False,
        websocket=False,
        before_request=False,
        check_existing=False,
    ):
        """Adds a route to the router.
        :param route: A string representation of the route
        :param endpoint: The endpoint for the route -- can be callable, or class.
        :param default: If ``True``, all unknown requests will route to this view.
        """
        if before_request:
            if websocket:
                self.before_requests.setdefault("ws", []).append(endpoint)
            else:
                self.before_requests.setdefault("http", []).append(endpoint)
            return

        if check_existing:
            assert not self.routes or route not in (
                item.route for item in self.routes
            ), f"Route '{route}' already exists"

        if default:
            self.default_endpoint = endpoint

        if websocket:
            route = WebSocketRoute(route, endpoint)
        else:
            route = Route(route, endpoint)

        self.routes.append(route)

    def mount(self, route, app):
        """Mounts ASGI / WSGI applications at a given route"""
        self.apps.update(route, app)

    def add_event_handler(self, event_type, handler):
        assert event_type in (
            "startup",
            "shutdown",
        ), f"Only 'startup' and 'shutdown' events are supported, not {event_type}."
        self.events[event_type].append(handler)

    async def trigger_event(self, event_type):
        for handler in self.events.get(event_type, []):
            if asyncio.iscoroutinefunction(handler):
                await handler()
            else:
                handler()

    def before_request(self, endpoint, websocket=False):
        if websocket:
            self.before_requests.setdefault("ws", []).append(endpoint)
        else:
            self.before_requests.setdefault("http", []).append(endpoint)

    def url_for(self, endpoint, **params):
        # TODO: Check for params
        for route in self.routes:
            if endpoint in (route.endpoint, route.endpoint.__name__):
                return route.url(**params)
        return None

    async def default_response(self, scope, receive, send):
        if scope["type"] == "websocket":
            websocket_close = WebSocketClose()
            await websocket_close(receive, send)
            return

        request = Request(scope, receive)
        response = Response(request, formats=get_formats())

        raise HTTPException(status_code=status_codes.HTTP_404)

    def _resolve_route(self, scope):
        for route in self.routes:
            matches, child_scope = route.matches(scope)
            if matches:
                scope.update(child_scope)
                return route
        return None

    async def lifespan(self, scope, receive, send):
        message = await receive()
        assert message["type"] == "lifespan.startup"

        try:
            await self.trigger_event("startup")
        except BaseException:
            msg = traceback.format_exc()
            await send({"type": "lifespan.startup.failed", "message": msg})
            raise

        await send({"type": "lifespan.startup.complete"})
        message = await receive()
        assert message["type"] == "lifespan.shutdown"
        await self.trigger_event("shutdown")
        await send({"type": "lifespan.shutdown.complete"})

    async def __call__(self, scope, receive, send):
        assert scope["type"] in ("http", "websocket", "lifespan")

        if scope["type"] == "lifespan":
            await self.lifespan(scope, receive, send)
            return

        path = scope["path"]
        root_path = scope.get("root_path", "")

        # Check "primary" mounted routes first (before submounted apps)
        route = self._resolve_route(scope)

        scope["before_requests"] = self.before_requests

        if route is not None:
            await route(scope, receive, send)
            return

        # Call into a submounted app, if one exists.
        for path_prefix, app in self.apps.items():
            if path.startswith(path_prefix):
                scope["path"] = path[len(path_prefix) :]
                scope["root_path"] = root_path + path_prefix
                try:
                    await app(scope, receive, send)
                    return
                except TypeError:
                    app = WSGIMiddleware(app)
                    await app(scope, receive, send)
                    return

        await self.default_endpoint(scope, receive, send)
