"""Standalone route declaration and composition.

:class:`Router` records route declarations *without* a live :class:`~responder.API`
instance, so routes can live in their own modules — like Flask blueprints or
FastAPI's ``APIRouter`` — and be attached later with ``api.include_router()``.
Routers nest (prefixes compose, tags merge, dependencies concatenate), carry
group-level defaults (``tags``, ``dependencies``, ``auth``), and may be included
into more than one prefix.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from .params import _Depends
from .routes import _is_async

__all__ = ["Router"]

#: Sentinel meaning "auth was not specified" — distinct from ``auth=None``,
#: which explicitly marks routes as public.
_AUTH_UNSET: Any = object()


def _normalize_prefix(prefix: str) -> str:
    """Normalize a mount prefix: ``""`` stays empty, else ``/like/this``."""
    if not prefix:
        return ""
    if not prefix.startswith("/"):
        raise ValueError(f"A router prefix must start with '/' (got {prefix!r})")
    return prefix.rstrip("/")


def _merge_tags(
    group_tags: tuple[str, ...], route_tags: tuple[str, ...]
) -> tuple[str, ...]:
    """Concatenate group tags before route tags, dropping duplicates in order."""
    merged: list[str] = []
    for tag in (*group_tags, *route_tags):
        if tag not in merged:
            merged.append(tag)
    return tuple(merged)


def _as_dependency_tuple(dependencies: Any) -> tuple[Any, ...]:
    if dependencies is None:
        return ()
    deps: tuple[Any, ...]
    if isinstance(dependencies, (list, tuple)):
        deps = tuple(dependencies)
    else:
        deps = (dependencies,)
    if any(not isinstance(d, _Depends) for d in deps):
        raise TypeError(
            "Route dependencies must be declared as Depends(...) markers."
        )
    return deps


def _prefix_scoped_hook(hook: Callable, prefix: str) -> Callable:
    """Wrap a before-request hook so it only runs for paths under ``prefix``."""

    def _in_scope(path: str) -> bool:
        return path == prefix or path.startswith(prefix + "/")

    scoped: Callable
    # ``_is_async`` (unlike ``iscoroutinefunction``) also detects a callable
    # instance whose ``__call__`` is async; classifying such a hook as sync
    # would silently discard its never-awaited coroutine.
    if _is_async(hook):

        async def _scoped_async(target: Any, *rest: Any) -> None:
            if _in_scope(target.url.path):
                await hook(target, *rest)

        scoped = _scoped_async
    else:

        def _scoped_sync(target: Any, *rest: Any) -> None:
            if _in_scope(target.url.path):
                hook(target, *rest)

        scoped = _scoped_sync

    functools.wraps(hook)(scoped)
    return scoped


@dataclass(frozen=True)
class _RouteDecl:
    """A recorded route declaration, replayed later through ``API.route()``."""

    route: str
    endpoint: Any
    tags: tuple[str, ...]
    dependencies: tuple[Any, ...]
    auth: Any
    options: dict[str, Any]

    def merged_under(
        self,
        prefix: str,
        tags: tuple[str, ...],
        dependencies: tuple[Any, ...],
        auth: Any,
    ) -> _RouteDecl:
        """Compose group-level values over this declaration (group values first)."""
        return _RouteDecl(
            route=f"{prefix}{self.route}",
            endpoint=self.endpoint,
            tags=_merge_tags(tags, self.tags),
            dependencies=(*dependencies, *self.dependencies),
            auth=self.auth if self.auth is not _AUTH_UNSET else auth,
            options=self.options,
        )


@dataclass(frozen=True)
class _HookDecl:
    """A recorded before-request hook, scoped to the router's mounted prefix."""

    hook: Callable
    websocket: bool
    prefix: str

    def merged_under(self, prefix: str) -> _HookDecl:
        return _HookDecl(self.hook, self.websocket, f"{prefix}{self.prefix}")


class Router:
    """A standalone, composable collection of route declarations.

    Unlike ``api.group()``, a ``Router`` needs no :class:`~responder.API` at
    creation: it records declarations and replays them when included, so routes
    can be declared in separate modules without circular imports::

        # users.py
        from responder import Router

        router = Router(prefix="/users", tags=["users"])

        @router.route("/{user_id:int}")
        def get_user(req, resp, *, user_id):
            resp.media = {"id": user_id}

        # app.py
        api.include_router(users.router, prefix="/v1")  # GET /v1/users/{user_id}

    Routers nest via :meth:`include_router`; prefixes compose, ``tags`` merge
    (group tags first, duplicates dropped), ``dependencies`` concatenate (group
    guards run first), and the innermost explicit ``auth`` wins. Inclusion is a
    snapshot: routes declared after a router has been included are not picked up
    by that earlier inclusion.

    :param prefix: URL prefix for every route in this router (e.g. ``"/users"``).
    :param tags: OpenAPI tags applied to every route in this router.
    :param dependencies: ``Depends(...)`` guards run before every route in this
                         router.
    :param auth: Auth helper(s) required by every route in this router
                 (individual routes may still override with their own ``auth=``).
    """

    def __init__(
        self,
        prefix: str = "",
        *,
        tags: Iterable[str] | None = None,
        dependencies: Any = None,
        auth: Any = _AUTH_UNSET,
    ) -> None:
        self.prefix = _normalize_prefix(prefix)
        self.tags: tuple[str, ...] = tuple(tags) if tags else ()
        self.dependencies = _as_dependency_tuple(dependencies)
        self.auth = auth
        self._routes: list[_RouteDecl] = []
        self._hooks: list[_HookDecl] = []

    def __repr__(self) -> str:
        return (
            f"<Router prefix={self.prefix or '/'!r} "
            f"routes={len(self._routes)} hooks={len(self._hooks)}>"
        )

    def route(
        self,
        route: str | None = None,
        *,
        tags: Iterable[str] | None = None,
        dependencies: Any = None,
        auth: Any = _AUTH_UNSET,
        **options: Any,
    ) -> Callable:
        """Decorator recording a route declaration; matches ``API.route()``.

        All keyword options (``methods=``, ``response_model=``, ``summary=``,
        ...) are stored and replayed through ``API.route()`` at inclusion time.
        """

        def decorator(f: Callable) -> Callable:
            self.add_route(
                route, f, tags=tags, dependencies=dependencies, auth=auth, **options
            )
            return f

        return decorator

    def add_route(
        self,
        route: str | None = None,
        endpoint: Any = None,
        *,
        tags: Iterable[str] | None = None,
        dependencies: Any = None,
        auth: Any = _AUTH_UNSET,
        before_request: bool = False,
        websocket: bool = False,
        **options: Any,
    ) -> None:
        """Record a route declaration (imperative form of :meth:`route`)."""
        if endpoint is None:
            raise ValueError("An endpoint is required to add a route")
        if before_request:
            self._hooks.append(_HookDecl(endpoint, websocket, ""))
            return
        if route is None:
            raise ValueError("A route path is required to add a route")
        if websocket:
            options["websocket"] = True
        self._routes.append(
            _RouteDecl(
                route=route,
                endpoint=endpoint,
                tags=tuple(tags) if tags else (),
                dependencies=_as_dependency_tuple(dependencies),
                auth=auth,
                options=options,
            )
        )

    def get(self, route: str | None = None, **options: Any) -> Callable:
        """Record a ``GET`` route (sugar for ``route(methods=["GET"])``)."""
        return self.route(route, methods=["GET"], **options)

    def post(self, route: str | None = None, **options: Any) -> Callable:
        """Record a ``POST`` route (sugar for ``route(methods=["POST"])``)."""
        return self.route(route, methods=["POST"], **options)

    def put(self, route: str | None = None, **options: Any) -> Callable:
        """Record a ``PUT`` route (sugar for ``route(methods=["PUT"])``)."""
        return self.route(route, methods=["PUT"], **options)

    def patch(self, route: str | None = None, **options: Any) -> Callable:
        """Record a ``PATCH`` route (sugar for ``route(methods=["PATCH"])``)."""
        return self.route(route, methods=["PATCH"], **options)

    def delete(self, route: str | None = None, **options: Any) -> Callable:
        """Record a ``DELETE`` route (sugar for ``route(methods=["DELETE"])``)."""
        return self.route(route, methods=["DELETE"], **options)

    def websocket_route(self, route: str | None = None, **options: Any) -> Callable:
        """Record a WebSocket route (sugar for ``route(websocket=True)``)."""
        return self.route(route, websocket=True, **options)

    def before_request(self, websocket: Any = False) -> Callable:
        """Record a before-request hook scoped to this router's mounted prefix.

        Works both bare and called (``@router.before_request`` or
        ``@router.before_request()``). Once included, the hook only runs for
        request paths under the prefix the router was mounted at; if the
        composed prefix is empty, it runs for every request.

        :param websocket: If ``True``, register as a WebSocket hook instead of
                          HTTP.
        """
        if callable(websocket):  # used bare: @router.before_request
            f = websocket
            self._hooks.append(_HookDecl(f, False, ""))
            return f

        def decorator(f: Callable) -> Callable:
            self._hooks.append(_HookDecl(f, bool(websocket), ""))
            return f

        return decorator

    def include_router(
        self,
        router: Router,
        *,
        prefix: str = "",
        tags: Iterable[str] | None = None,
        dependencies: Any = None,
        auth: Any = _AUTH_UNSET,
    ) -> None:
        """Nest another router's declarations under this one.

        The child's declarations are copied at call time (a snapshot): routes
        added to the child afterwards are not seen. Prefixes compose, ``tags``
        merge, ``dependencies`` concatenate, and a child route's own ``auth``
        wins over the values given here.

        :param router: The :class:`Router` to include.
        :param prefix: Extra URL prefix, prepended to the child's own prefix.
        :param tags: OpenAPI tags to merge into the included routes.
        :param dependencies: ``Depends(...)`` guards prepended to the included
                             routes' dependencies.
        :param auth: Auth helper(s) for included routes that don't set their own.
        """
        if not isinstance(router, Router):
            raise TypeError(
                f"include_router() expects a responder.Router, "
                f"got {type(router).__name__}"
            )
        if router is self:
            raise ValueError("A router cannot include itself")
        prefix = _normalize_prefix(prefix)
        include_tags = tuple(tags) if tags else ()
        include_deps = _as_dependency_tuple(dependencies)
        for decl in router._effective_routes():
            self._routes.append(
                decl.merged_under(prefix, include_tags, include_deps, auth)
            )
        for hook in router._effective_hooks():
            self._hooks.append(hook.merged_under(prefix))

    def _effective_routes(self) -> Iterator[_RouteDecl]:
        """Yield route declarations with this router's own group values applied."""
        for decl in self._routes:
            yield decl.merged_under(self.prefix, self.tags, self.dependencies, self.auth)

    def _effective_hooks(self) -> Iterator[_HookDecl]:
        """Yield hook declarations scoped under this router's own prefix."""
        for hook in self._hooks:
            yield hook.merged_under(self.prefix)
