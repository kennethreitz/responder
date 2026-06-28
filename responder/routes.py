from __future__ import annotations

import asyncio
import inspect
import logging
import re
import traceback
import weakref
from collections import defaultdict
from collections.abc import Callable
from typing import Any, Union

__all__ = ["Route", "WebSocketRoute", "Router"]

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.responses import Response as StarletteResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket, WebSocketClose, WebSocketState

from . import status_codes
from .formats import get_formats
from .models import Request, Response

logger = logging.getLogger("responder")

_UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

_CONVERTORS = {
    "int": (int, r"\d+"),
    "str": (str, r"[^/]+"),
    "float": (float, r"\d+(?:\.\d+)?"),
    "path": (str, r".+"),
    "uuid": (str, _UUID_RE),
}

PARAM_RE = re.compile("{([a-zA-Z_][a-zA-Z0-9_]*)(:[a-zA-Z_][a-zA-Z0-9_]*)?}")


def compile_path(path: str) -> tuple[re.Pattern, dict[str, type]]:
    path_re = "^"
    param_convertors: dict[str, type] = {}
    idx = 0

    for match in PARAM_RE.finditer(path):
        param_name, convertor_type = match.groups(default="str")
        convertor_type = convertor_type.lstrip(":")
        assert convertor_type in _CONVERTORS.keys(), (
            f"Unknown path convertor '{convertor_type}'"
        )
        convertor, convertor_re = _CONVERTORS[convertor_type]

        path_re += re.escape(path[idx : match.start()])
        path_re += rf"(?P<{param_name}>{convertor_re})"

        param_convertors[param_name] = convertor

        idx = match.end()

    path_re += re.escape(path[idx:]) + "$"

    return re.compile(path_re), param_convertors


_VIEW_PARAM_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _view_param_names(view: Callable, skip: int = 2) -> tuple[str, ...]:
    """Return the names of a view's parameters beyond the first ``skip``
    (``req, resp`` for HTTP views, ``ws`` for WebSocket handlers).

    Results are cached per underlying function, since signature inspection
    is comparatively expensive and views never change shape at runtime.
    """
    cache_key = getattr(view, "__func__", view)
    try:
        return _VIEW_PARAM_CACHE[cache_key][skip:]
    except (KeyError, TypeError):
        pass

    try:
        parameters = inspect.signature(view).parameters
    except (TypeError, ValueError):
        return ()
    names = []
    for param in parameters.values():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        names.append(param.name)

    result = tuple(names)
    try:
        _VIEW_PARAM_CACHE[cache_key] = result
    except TypeError:
        pass
    return result[skip:]


async def _resolve_dependency(provider: Callable, request) -> tuple[Any, Callable | None]:
    """Call a dependency provider, returning (value, teardown).

    Providers may be sync or async functions, or sync/async generators that
    yield a value and resume for teardown (like ``contextlib.contextmanager``).
    Providers taking at least one parameter receive the current request
    (or the WebSocket, for WebSocket routes).
    """
    try:
        takes_request = bool(inspect.signature(provider).parameters)
    except (TypeError, ValueError):
        takes_request = False
    args = (request,) if takes_request else ()

    if inspect.isasyncgenfunction(provider):
        agen = provider(*args)
        value = await agen.__anext__()

        async def teardown_async():
            try:
                await agen.__anext__()
            except StopAsyncIteration:
                pass

        return value, teardown_async

    if inspect.isgeneratorfunction(provider):
        gen = provider(*args)
        value = await run_in_threadpool(next, gen)

        async def teardown_sync():
            await run_in_threadpool(lambda: next(gen, None))

        return value, teardown_sync

    if inspect.iscoroutinefunction(provider):
        return await provider(*args), None

    return await run_in_threadpool(provider, *args), None


def _accepts_json(scope: Scope) -> bool:
    """Whether the request's Accept header asks for JSON."""
    for key, value in scope.get("headers", []):
        if key == b"accept":
            return b"json" in value
    return False


class BaseRoute:
    route: str
    endpoint: Callable

    def url(self, **params: Any) -> str:
        raise NotImplementedError()

    def matches(self, scope: Scope) -> tuple[bool, dict]:
        raise NotImplementedError()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        raise NotImplementedError()


class Route(BaseRoute):
    """An HTTP route that maps a URL pattern to an endpoint.

    Supports path parameters with type convertors (``{id:int}``, ``{slug:str}``,
    ``{pk:uuid}``, ``{value:float}``, ``{rest:path}``).
    """

    def __init__(
        self,
        route: str,
        endpoint: Callable,
        *,
        before_request: bool = False,
        methods: list[str] | None = None,
    ) -> None:
        assert route.startswith("/"), "Route path must start with '/'"
        self.route = route
        self.endpoint = endpoint
        self.before_request = before_request
        self.methods: set[str] | None = {m.upper() for m in methods} if methods else None

        self.path_re: re.Pattern
        self.param_convertors: dict[str, type]
        self.path_re, self.param_convertors = compile_path(route)
        # Strip type annotations for URL generation (e.g. {id:int} -> {id})
        self._url_template = PARAM_RE.sub(r"{\1}", route)

    def __repr__(self) -> str:
        return f"<Route {self.route!r}={self.endpoint!r}>"

    def url(self, **params: Any) -> str:
        return self._url_template.format(**params)

    @property
    def path_template(self) -> str:
        """The route with convertor annotations stripped (``/users/{id}``)."""
        return self._url_template

    @property
    def endpoint_name(self) -> str:
        return self.endpoint.__name__

    @property
    def description(self) -> str | None:
        return self.endpoint.__doc__

    def matches(self, scope: Scope) -> tuple[bool, dict]:
        if scope["type"] != "http":
            return False, {}

        if self.methods:
            method = scope.get("method", "").upper()
            # HEAD is implicitly supported wherever GET is.
            if method not in self.methods and not (
                method == "HEAD" and "GET" in self.methods
            ):
                return False, {}

        path = scope["path"]
        match = self.path_re.match(path)

        if match is None:
            return False, {}

        matched_params = match.groupdict()
        for key, value in matched_params.items():
            matched_params[key] = self.param_convertors[key](value)

        return True, {"path_params": {**matched_params}}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        formats = scope.get("formats") or get_formats()
        request = Request(scope, receive, api=scope.get("api"), formats=formats)
        response = Response(
            req=request, formats=formats, auto_etag=scope.get("auto_etag", False)
        )

        path_params = scope.get("path_params", {})
        before_requests = scope.get("before_requests", {"http": [], "ws": []})

        for before_request in before_requests.get("http", []):
            if inspect.iscoroutinefunction(before_request):
                await before_request(request, response)
            else:
                await run_in_threadpool(before_request, request, response)
            # If a before_request hook set a status code, short-circuit
            if response.status_code is not None:
                await response(scope, receive, send)
                return

        # Auto-validate query parameters with Pydantic model
        params_model = getattr(self.endpoint, "_params_model", None)
        if params_model is not None:
            data = {}
            for key in request.params:
                values = request.params.get_list(key)
                data[key] = values if len(values) > 1 else values[-1]
            try:
                request.state.validated_params = params_model(**data)
            except Exception as exc:
                response.status_code = 422
                if hasattr(exc, "errors"):
                    errors = exc.errors()
                else:
                    errors = [{"msg": str(exc)}]
                response.media = {"errors": errors}
                await response(scope, receive, send)
                return

        # Auto-validate request body with Pydantic model
        req_model = getattr(self.endpoint, "_request_model", None)
        if req_model is not None and request.method in ("post", "put", "patch", "delete"):
            try:
                body = await request.media()
                if not isinstance(body, dict):
                    raise TypeError("Request body must be a JSON object")
                request.state.validated = req_model(**body)
            except HTTPException:
                raise  # e.g. 413 from the request-size limit — not a 422
            except Exception as exc:
                response.status_code = 422
                errors = []
                if hasattr(exc, "errors"):
                    errors = exc.errors()
                else:
                    errors = [{"msg": str(exc)}]
                response.media = {"errors": errors}
                await response(scope, receive, send)
                return

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
            except AttributeError as ex:
                if on_request is None:
                    raise HTTPException(status_code=status_codes.HTTP_405) from ex  # type: ignore[attr-defined]
        else:
            views.append(self.endpoint)

        dependencies = scope.get("dependencies") or {}
        app_deps = scope.get("app_dependencies")
        resolved: dict[str, Any] = {}
        teardowns: list[Callable] = []

        async def run_views():
            for view in views:
                kwargs = dict(path_params)

                if dependencies:
                    for name in _view_param_names(view):
                        if name in kwargs or name not in dependencies:
                            continue
                        if name not in resolved:
                            provider, dep_scope = dependencies[name]
                            if dep_scope == "app" and app_deps is not None:
                                resolved[name] = await app_deps.resolve(name, provider)
                            else:
                                value, teardown = await _resolve_dependency(
                                    provider, request
                                )
                                resolved[name] = value
                                if teardown is not None:
                                    teardowns.append(teardown)
                        kwargs[name] = resolved[name]

                # Check __call__ for class-based views (e.g. GraphQL)
                if inspect.iscoroutinefunction(view) or inspect.iscoroutinefunction(
                    view.__call__
                ):
                    result = await view(request, response, **kwargs)
                else:
                    result = await run_in_threadpool(view, request, response, **kwargs)

                # Returned values set the response body, like Flask/FastAPI.
                if result is not None:
                    if isinstance(result, (dict, list)):
                        response.media = result
                    elif isinstance(result, str):
                        response.text = result
                    elif isinstance(result, bytes):
                        response.content = result

        timeout = scope.get("request_timeout")

        try:
            if timeout:
                try:
                    await asyncio.wait_for(run_views(), timeout)
                except asyncio.TimeoutError:
                    response.status_code = 504
                    if _accepts_json(scope):
                        response.media = {"error": "Request timed out"}
                    else:
                        response.text = "Request timed out"
                    await response(scope, receive, send)
                    return
            else:
                await run_views()

            # Auto-serialize response with Pydantic model
            resp_model = getattr(self.endpoint, "_response_model", None)
            if resp_model is not None and isinstance(response.media, dict):
                try:
                    validated = resp_model(**response.media)
                    response.media = validated.model_dump()
                except (ValueError, TypeError):
                    pass  # Don't break the response if serialization fails

            # Run after-request hooks
            after_requests = scope.get("after_requests", [])
            for after_request in after_requests:
                if inspect.iscoroutinefunction(after_request):
                    await after_request(request, response)
                else:
                    await run_in_threadpool(after_request, request, response)

            if response.status_code is None:
                response.status_code = status_codes.HTTP_200  # type: ignore[attr-defined]

            await response(scope, receive, send)
        finally:
            # Best-effort cleanup: one failing teardown must not strand the
            # rest (they hold the very resources the feature exists to release).
            for teardown in reversed(teardowns):
                try:
                    await teardown()
                except Exception:
                    logger.exception("Dependency teardown failed")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Route):
            return NotImplemented
        return self.route == other.route and self.endpoint == other.endpoint

    def __hash__(self) -> int:
        return hash(self.route) ^ hash(self.endpoint) ^ hash(self.before_request)


class WebSocketRoute(BaseRoute):
    """A WebSocket route that maps a URL pattern to a WebSocket handler."""

    def __init__(
        self, route: str, endpoint: Callable, *, before_request: bool = False
    ) -> None:
        assert route.startswith("/"), "Route path must start with '/'"
        self.route = route
        self.endpoint = endpoint
        self.before_request = before_request

        self.path_re: re.Pattern
        self.param_convertors: dict[str, type]
        self.path_re, self.param_convertors = compile_path(route)
        self._url_template = PARAM_RE.sub(r"{\1}", route)

    def __repr__(self) -> str:
        return f"<Route {self.route!r}={self.endpoint!r}>"

    def url(self, **params: Any) -> str:
        return self._url_template.format(**params)

    @property
    def path_template(self) -> str:
        """The route with convertor annotations stripped (``/ws/{room}``)."""
        return self._url_template

    @property
    def endpoint_name(self) -> str:
        return self.endpoint.__name__

    @property
    def description(self) -> str | None:
        return self.endpoint.__doc__

    def matches(self, scope: Scope) -> tuple[bool, dict]:
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

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        ws = WebSocket(scope, receive, send)

        before_requests = scope.get("before_requests", {"http": [], "ws": []})
        for before_request in before_requests.get("ws", []):
            if inspect.iscoroutinefunction(before_request):
                await before_request(ws)
            else:
                await run_in_threadpool(before_request, ws)
            # If a hook closed the connection, short-circuit the endpoint.
            if WebSocketState.DISCONNECTED in (ws.client_state, ws.application_state):
                return

        # Inject path params and dependencies the handler asks for by name.
        # (Only declared names are passed, so `handler(ws)` keeps working.)
        path_params = scope.get("path_params", {})
        dependencies = scope.get("dependencies") or {}
        app_deps = scope.get("app_dependencies")
        kwargs: dict[str, Any] = {}
        teardowns: list[Callable] = []

        try:
            for name in _view_param_names(self.endpoint, skip=1):
                if name in path_params:
                    kwargs[name] = path_params[name]
                elif name in dependencies:
                    provider, dep_scope = dependencies[name]
                    if dep_scope == "app" and app_deps is not None:
                        kwargs[name] = await app_deps.resolve(name, provider)
                    else:
                        value, teardown = await _resolve_dependency(provider, ws)
                        kwargs[name] = value
                        if teardown is not None:
                            teardowns.append(teardown)

            await self.endpoint(ws, **kwargs)
        finally:
            for teardown in reversed(teardowns):
                try:
                    await teardown()
                except Exception:
                    logger.exception("Dependency teardown failed")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, WebSocketRoute):
            return NotImplemented
        return self.route == other.route and self.endpoint == other.endpoint

    def __hash__(self) -> int:
        return hash(self.route) ^ hash(self.endpoint) ^ hash(self.before_request)


class _AppDependencyState:
    """Holds app-scoped dependency values for the lifetime of the application."""

    __slots__ = ("cache", "lock", "teardowns")

    def __init__(self) -> None:
        self.cache: dict[str, Any] = {}
        self.lock = asyncio.Lock()
        self.teardowns: list[Callable] = []

    async def resolve(self, name: str, provider: Callable) -> Any:
        if name in self.cache:
            return self.cache[name]
        async with self.lock:
            if name not in self.cache:
                value, teardown = await _resolve_dependency(provider, None)
                self.cache[name] = value
                if teardown is not None:
                    self.teardowns.append(teardown)
        return self.cache[name]

    async def shutdown(self) -> None:
        try:
            while self.teardowns:
                teardown = self.teardowns.pop()
                try:
                    await teardown()
                except Exception:
                    logger.exception("App-scoped dependency teardown failed")
        finally:
            self.cache.clear()


class Router:
    """The core router that dispatches incoming requests to matching routes.

    Handles route matching, before/after request hooks, lifespan events,
    and mounted sub-applications.
    """

    def __init__(
        self,
        routes: list[BaseRoute] | None = None,
        default_response: Callable | None = None,
        before_requests: dict[str, list[Callable]] | None = None,
        lifespan: Callable | None = None,
        formats: dict[str, Callable] | None = None,
        redirect_slashes: bool = True,
        max_request_size: int | None = None,
        auto_etag: bool = False,
        request_timeout: float | None = None,
    ) -> None:
        self.routes: list[BaseRoute] = [] if routes is None else list(routes)

        self.apps: dict[str, Union[ASGIApp, Any]] = {}
        self.default_endpoint: Callable = (
            self.default_response if default_response is None else default_response
        )
        self.before_requests: dict[str, list[Callable]] = (
            {"http": [], "ws": []} if before_requests is None else before_requests
        )
        self.after_requests: list[Callable] = []
        self.events: defaultdict[str, list[Callable]] = defaultdict(list)
        self.dependencies: dict[str, tuple[Callable, str]] = {}
        self.app_dependencies = _AppDependencyState()
        self.api: Any = None  # Set by API.__init__; reaches views as req.api.
        self.redirect_slashes = redirect_slashes
        self.max_request_size = max_request_size
        self.auto_etag = auto_etag
        self.request_timeout = request_timeout
        self._route_cache: dict[tuple[str, str], tuple[BaseRoute, dict]] = {}
        self.formats: dict[str, Callable] = (
            get_formats() if formats is None else formats
        )
        self._lifespan_handler = lifespan

    def add_route(
        self,
        route: str | None = None,
        endpoint: Callable | None = None,
        *,
        default: bool = False,
        websocket: bool = False,
        before_request: bool = False,
        check_existing: bool = False,
        methods: list[str] | None = None,
    ) -> None:
        """Adds a route to the router.
        :param route: A string representation of the route
        :param endpoint: The endpoint for the route -- can be callable, or class.
        :param default: If ``True``, all unknown requests will route to this view.
        :param methods: Optional list of HTTP methods (e.g. ["GET", "POST"]).
        """
        if endpoint is None:
            raise ValueError("An endpoint is required to add a route")

        if before_request:
            if websocket:
                self.before_requests.setdefault("ws", []).append(endpoint)
            else:
                self.before_requests.setdefault("http", []).append(endpoint)
            return

        if route is None:
            raise ValueError("A route path is required to add a route")

        if check_existing and any(item.route == route for item in self.routes):
            raise ValueError(f"Route '{route}' already exists")

        if default:
            self.default_endpoint = endpoint

        new_route: BaseRoute
        if websocket:
            new_route = WebSocketRoute(route, endpoint)
        else:
            new_route = Route(route, endpoint, methods=methods)

        self.routes.append(new_route)
        self._route_cache.clear()

    def mount(self, route: str, app: Any) -> None:
        """Mounts ASGI / WSGI applications at a given route"""
        self.apps.update({route: app})

    def add_event_handler(self, event_type: str, handler: Callable) -> None:
        assert event_type in (
            "startup",
            "shutdown",
        ), f"Only 'startup' and 'shutdown' events are supported, not {event_type}."
        self.events[event_type].append(handler)

    async def trigger_event(self, event_type: str) -> None:
        for handler in self.events.get(event_type, []):
            if inspect.iscoroutinefunction(handler):
                await handler()
            else:
                handler()

    def add_dependency(
        self, name: str, provider: Callable, scope: str = "request"
    ) -> None:
        """Register a dependency provider, injectable into views by parameter name.

        :param scope: ``"request"`` (resolved per request, the default) or
                      ``"app"`` (resolved once, torn down at shutdown).
        """
        if scope not in ("request", "app"):
            raise ValueError(
                f"Dependency scope must be 'request' or 'app', not {scope!r}"
            )
        if scope == "app":
            try:
                takes_args = bool(inspect.signature(provider).parameters)
            except (TypeError, ValueError):
                takes_args = False
            if takes_args:
                raise ValueError(
                    "App-scoped dependency providers cannot take parameters — "
                    "they outlive any single request"
                )
        self.dependencies[name] = (provider, scope)

    def before_request(self, endpoint: Callable, websocket: bool = False) -> None:
        if websocket:
            self.before_requests.setdefault("ws", []).append(endpoint)
        else:
            self.before_requests.setdefault("http", []).append(endpoint)

    def after_request(self, endpoint: Callable) -> None:
        self.after_requests.append(endpoint)

    def url_for(self, endpoint: Callable | str, **params: Any) -> str | None:
        for route in self.routes:
            if endpoint in (route.endpoint, route.endpoint.__name__):
                return route.url(**params)
        return None

    async def default_response(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            websocket_close = WebSocketClose()
            await websocket_close(scope, receive, send)
            return

        request = Request(scope, receive)
        response = Response(request, formats=get_formats())  # noqa: F841

        raise HTTPException(status_code=status_codes.HTTP_404)  # type: ignore[attr-defined]

    def _resolve_route(self, scope: Scope) -> BaseRoute | None:
        key = (scope.get("method", "ws"), scope["path"])
        cached = self._route_cache.get(key)
        if cached is not None:
            route, child_scope = cached
            # Copy path_params so per-request mutation can't poison the cache.
            scope.update(
                {k: dict(v) if isinstance(v, dict) else v for k, v in child_scope.items()}
            )
            scope["route_pattern"] = getattr(route, "path_template", route.route)
            return route

        for route in self.routes:
            matches, child_scope = route.matches(scope)
            if matches:
                scope.update(child_scope)
                scope["route_pattern"] = getattr(route, "path_template", route.route)
                if len(self._route_cache) >= 1024:
                    self._route_cache.clear()
                self._route_cache[key] = (route, child_scope)
                return route
        return None

    async def lifespan(self, scope: Scope, receive: Receive, send: Send) -> None:
        message = await receive()
        assert message["type"] == "lifespan.startup"

        if self._lifespan_handler is not None:
            # Modern lifespan context manager pattern
            try:
                ctx = self._lifespan_handler(scope.get("app"))
                await ctx.__aenter__()
            except BaseException:
                msg = traceback.format_exc()
                await send({"type": "lifespan.startup.failed", "message": msg})
                raise

            await send({"type": "lifespan.startup.complete"})
            message = await receive()
            assert message["type"] == "lifespan.shutdown"

            await ctx.__aexit__(None, None, None)
            await self.app_dependencies.shutdown()
        else:
            # Legacy on_event("startup") / on_event("shutdown") pattern
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
            await self.app_dependencies.shutdown()

        await send({"type": "lifespan.shutdown.complete"})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        assert scope["type"] in ("http", "websocket", "lifespan")

        if scope["type"] == "lifespan":
            await self.lifespan(scope, receive, send)
            return

        await self._dispatch(scope, receive, send)

    async def _dispatch(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope["path"]
        root_path = scope.get("root_path", "")

        # Check "primary" mounted routes first (before submounted apps)
        route = self._resolve_route(scope)

        scope["before_requests"] = self.before_requests
        scope["after_requests"] = self.after_requests
        scope["dependencies"] = self.dependencies
        scope["app_dependencies"] = self.app_dependencies
        scope["formats"] = self.formats
        scope["api"] = self.api
        scope["max_request_size"] = self.max_request_size
        scope["auto_etag"] = self.auto_etag
        scope["request_timeout"] = self.request_timeout

        if route is not None:
            await route(scope, receive, send)
            return

        # Call into a submounted app, if one exists. Longer (more specific)
        # prefixes win, and a prefix only matches on a path-segment boundary
        # so that, e.g., "/subscribe" is not mis-routed into a mount at "/sub"
        # (the empty-prefix root catch-all still matches everything).
        for path_prefix, app in sorted(
            self.apps.items(), key=lambda kv: len(kv[0]), reverse=True
        ):
            if path == path_prefix or path.startswith(path_prefix + "/"):
                scope["path"] = path[len(path_prefix) :] or "/"
                scope["root_path"] = root_path + path_prefix

                if not (inspect.iscoroutinefunction(app) or hasattr(app, "__asgi_app__")):
                    # Check if it looks like a WSGI app (callable with fewer params)
                    try:
                        await app(scope, receive, send)
                        return
                    except TypeError as exc:
                        # Only fall back to WSGI if the error is about call signature
                        if "argument" not in str(exc) and "positional" not in str(exc):
                            raise
                        from typing import cast

                        from a2wsgi import WSGIMiddleware

                        wsgi_app = WSGIMiddleware(cast(Any, app))
                        await cast(Any, wsgi_app)(scope, receive, send)
                        return
                else:
                    await app(scope, receive, send)
                    return

        # A near-miss on the trailing slash gets redirected to the real route,
        # preserving the method and query string (307).
        if scope["type"] == "http" and self.redirect_slashes and path != "/":
            alternate = path[:-1] if path.endswith("/") else path + "/"
            alternate_scope = dict(scope, path=alternate)
            if any(route.matches(alternate_scope)[0] for route in self.routes):
                query_string = scope.get("query_string", b"")
                location = alternate + (
                    f"?{query_string.decode('latin-1')}" if query_string else ""
                )
                redirect = StarletteResponse(
                    status_code=307, headers={"Location": location}
                )
                await redirect(scope, receive, send)
                return

        # The path exists but no route accepts this method: answer OPTIONS
        # with the allowed methods, and everything else with 405.
        if scope["type"] == "http":
            allowed = self._allowed_methods(path)
            if allowed:
                headers = {"Allow": ", ".join(sorted(allowed))}
                response: StarletteResponse
                if scope.get("method", "").upper() == "OPTIONS":
                    response = StarletteResponse(status_code=200, headers=headers)
                elif _accepts_json(scope):
                    response = JSONResponse(
                        {"error": "Method Not Allowed"},
                        status_code=status_codes.HTTP_405,  # type: ignore[attr-defined]
                        headers=headers,
                    )
                else:
                    response = StarletteResponse(
                        content="Method Not Allowed",
                        status_code=status_codes.HTTP_405,  # type: ignore[attr-defined]
                        headers=headers,
                    )
                await response(scope, receive, send)
                return

        await self.default_endpoint(scope, receive, send)

    def _allowed_methods(self, path: str) -> set[str]:
        """The union of methods accepted by method-restricted routes matching ``path``."""
        allowed: set[str] = set()
        for route in self.routes:
            if isinstance(route, Route) and route.methods and route.path_re.match(path):
                allowed.update(route.methods)
        if allowed:
            if "GET" in allowed:
                allowed.add("HEAD")
            allowed.add("OPTIONS")
        return allowed
