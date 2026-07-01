from __future__ import annotations

import asyncio
import dataclasses
import inspect
import logging
import re
import traceback
import weakref
from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any, Union

__all__ = [
    "Route",
    "WebSocketRoute",
    "Router",
    "DependencyError",
    "DependencyCycleError",
    "DependencyScopeError",
    "DependencyResolutionError",
]

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.responses import Response as StarletteResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket, WebSocketClose, WebSocketState

from . import status_codes
from .errors import (
    INTERNAL_SERVER_ERROR,
    PROBLEM_JSON,
    legacy_error_payload,
    problem_bytes_for,
    problem_payload_for,
)
from .formats import get_formats
from .models import Request, Response
from .params import _Depends

logger = logging.getLogger("responder")


class DependencyError(Exception):
    """Base class for dependency-injection configuration/resolution errors."""


class DependencyCycleError(DependencyError):
    """A dependency depends on itself (directly or transitively)."""


class DependencyScopeError(DependencyError):
    """An app-scoped dependency illegally depends on the request or a
    request-scoped dependency."""


class DependencyResolutionError(DependencyError):
    """A dependency parameter is neither the request nor a registered
    dependency."""

_UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

_CONVERTORS = {
    "int": (int, r"\d+"),
    "str": (str, r"[^/]+"),
    "float": (float, r"\d+(?:\.\d+)?"),
    "path": (str, r".+"),
    "uuid": (str, _UUID_RE),
}

PARAM_RE = re.compile("{([a-zA-Z_][a-zA-Z0-9_]*)(:[a-zA-Z_][a-zA-Z0-9_]*)?}")


def compile_path(path: str) -> tuple[re.Pattern, dict[str, type], dict[str, str]]:
    path_re = "^"
    param_convertors: dict[str, type] = {}
    param_convertor_names: dict[str, str] = {}
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
        param_convertor_names[param_name] = convertor_type

        idx = match.end()

    path_re += re.escape(path[idx:]) + "$"

    return re.compile(path_re), param_convertors, param_convertor_names


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


_VIEW_HINTS_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _view_type_hints(view: Callable) -> dict:
    """Resolved type hints for a view, cached per underlying function.

    Returns an empty dict if hints can't be resolved (e.g. an unresolvable
    forward reference), so type-hint features degrade gracefully.
    """
    cache_key = getattr(view, "__func__", view)
    try:
        return _VIEW_HINTS_CACHE[cache_key]
    except (KeyError, TypeError):
        pass
    try:
        import typing

        hints = typing.get_type_hints(view)
    except Exception:
        hints = {}
    try:
        _VIEW_HINTS_CACHE[cache_key] = hints
    except TypeError:
        pass
    return hints


def _is_pydantic_model(tp: Any) -> bool:
    """Whether ``tp`` is a Pydantic ``BaseModel`` subclass (duck-typed)."""
    return (
        isinstance(tp, type)
        and hasattr(tp, "model_validate")
        and hasattr(tp, "model_fields")
    )


_RESPONSE_ADAPTER_CACHE: dict = {}


def _response_type_adapter(tp):
    """A cached ``TypeAdapter`` for a generic response_model (list/union/etc.)."""
    try:
        return _RESPONSE_ADAPTER_CACHE[tp]
    except (KeyError, TypeError):
        pass
    from pydantic import TypeAdapter

    adapter = TypeAdapter(tp)
    try:
        _RESPONSE_ADAPTER_CACHE[tp] = adapter
    except TypeError:
        pass
    return adapter


_REQUEST_TYPES = (Request, WebSocket)
_HTTP_REQUEST_NAMES = frozenset({"req", "request"})
_WS_REQUEST_NAMES = frozenset({"ws", "websocket", "req", "request"})
_RESERVED_DEP_NAMES = frozenset(
    {"req", "request", "resp", "response", "ws", "websocket"}
)
_AUTH_INJECTION_NAMES = frozenset({"auth", "principal", "user"})

_DEP_PARAMS_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _dep_param_specs(provider: Callable) -> tuple[tuple[str, Any], ...]:
    """Cached ``(name, annotation)`` specs for a provider's injectable params."""
    key = getattr(provider, "__func__", provider)
    try:
        return _DEP_PARAMS_CACHE[key]
    except (KeyError, TypeError):
        pass
    try:
        params = inspect.signature(provider).parameters
    except (TypeError, ValueError):
        specs: tuple[tuple[str, Any], ...] = ()
    else:
        hints = _view_type_hints(provider)
        specs = tuple(
            (n, hints.get(n))
            for n, p in params.items()
            if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
        )
    try:
        _DEP_PARAMS_CACHE[key] = specs
    except TypeError:
        pass
    return specs


_DEPENDS_PARAMS_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _depends_params(view: Callable) -> dict[str, _Depends]:
    """Cached ``{param_name: Depends(...)}`` for a view/provider's inline
    ``Depends`` defaults. Signature inspection is comparatively expensive and
    runs on the per-request path."""
    key = getattr(view, "__func__", view)
    try:
        return _DEPENDS_PARAMS_CACHE[key]
    except (KeyError, TypeError):
        pass
    try:
        params = inspect.signature(view).parameters
    except (TypeError, ValueError):
        result: dict[str, _Depends] = {}
    else:
        result = {
            name: param.default
            for name, param in params.items()
            if isinstance(param.default, _Depends)
        }
    try:
        _DEPENDS_PARAMS_CACHE[key] = result
    except TypeError:
        pass
    return result


_BODY_MODEL_CANDIDATES_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _body_model_candidates(endpoint: Callable) -> tuple[tuple[str, Any], ...]:
    """Cached ``(name, model)`` pairs for an endpoint's Pydantic-model body
    parameters that have no default. The per-request path/dependency/auth
    filtering is applied by the caller; only the signature/hint inspection is
    memoized here (it runs on every write request otherwise)."""
    key = getattr(endpoint, "__func__", endpoint)
    try:
        return _BODY_MODEL_CANDIDATES_CACHE[key]
    except (KeyError, TypeError):
        pass
    hints = _view_type_hints(endpoint)
    try:
        sig_params: Any = inspect.signature(endpoint).parameters
    except (TypeError, ValueError):
        sig_params = {}
    candidates = tuple(
        (name, hints[name])
        for name in _view_param_names(endpoint)
        if _is_pydantic_model(hints.get(name))
        and (
            name not in sig_params
            or sig_params[name].default is inspect.Parameter.empty
        )
    )
    try:
        _BODY_MODEL_CANDIDATES_CACHE[key] = candidates
    except TypeError:
        pass
    return candidates


def _accepts_arg_count(view: Callable, count: int) -> bool:
    try:
        params = inspect.signature(view).parameters.values()
    except (TypeError, ValueError):
        return True
    positional = [
        p
        for p in params
        if p.kind
        in (
            p.POSITIONAL_ONLY,
            p.POSITIONAL_OR_KEYWORD,
        )
    ]
    return any(p.kind == p.VAR_POSITIONAL for p in params) or len(positional) >= count


def _is_request_param(name: str, annotation: Any, names: frozenset[str]) -> bool:
    """Whether a provider parameter should receive the request/websocket."""
    if name in names:
        return True
    return isinstance(annotation, type) and issubclass(annotation, _REQUEST_TYPES)


_STRINGY_PATH_CONVERTORS = frozenset({"str", "path", "uuid"})
_PATH_PARAM_ADAPTERS_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _path_param_adapters(view: Callable) -> dict[str, Any]:
    """Pydantic adapters for bare, same-name path params on ``view``.

    Explicit ``Path(...)`` markers are excluded here; they are resolved by the
    marker pipeline so aliases, metadata, and constraints stay centralized.
    """
    key = getattr(view, "__func__", view)
    try:
        return _PATH_PARAM_ADAPTERS_CACHE[key]
    except (KeyError, TypeError):
        pass

    try:
        from pydantic import TypeAdapter
    except ImportError:  # pragma: no cover - pydantic is a core dep
        return {}

    from .params import marker_params

    hints = _view_type_hints(view)
    explicit_path_params = {
        spec.name for spec in marker_params(view, hints) if spec.location == "path"
    }
    adapters: dict[str, Any] = {}
    parameters: Any
    try:
        parameters = inspect.signature(view).parameters
    except (TypeError, ValueError):
        parameters = {}
    for param in parameters.values():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if param.name in _RESERVED_DEP_NAMES or param.name in explicit_path_params:
            continue
        annotation = hints.get(param.name)
        if annotation is None:
            continue
        try:
            adapter = TypeAdapter(annotation)
        except Exception:
            adapter = None
        if adapter is not None:
            adapters[param.name] = adapter

    try:
        _PATH_PARAM_ADAPTERS_CACHE[key] = adapters
    except TypeError:
        pass
    return adapters


def _coerce_typed_path_params(
    view: Callable, path_params: dict[str, Any], convertor_names: dict[str, str]
) -> dict[str, Any]:
    """Validate/coerce bare same-name path params from handler annotations.

    This only applies to string-like route segments (plain ``{id}``, ``{id:path}``,
    ``{id:uuid}``) so explicit route convertors such as ``{id:int}`` keep their
    existing runtime behavior unless the user opts into ``Path(...)`` markers.
    """
    adapters = _path_param_adapters(view)
    if not adapters:
        return {}

    values: dict[str, Any] = {}
    errors: list[dict] = []
    for name, raw in path_params.items():
        if convertor_names.get(name) not in _STRINGY_PATH_CONVERTORS:
            continue
        adapter = adapters.get(name)
        if adapter is None:
            continue
        try:
            values[name] = adapter.validate_python(raw)
        except Exception as exc:
            if hasattr(exc, "errors"):
                for err in exc.errors():
                    err = dict(err)
                    err["loc"] = ["path", name]
                    errors.append(err)
            else:
                errors.append({"loc": ["path", name], "msg": str(exc)})
    if errors:
        raise _MarkerValidationError(errors)
    return values


class _MarkerValidationError(Exception):
    """Carries aggregated 422 errors from Query/Header/Cookie/Path markers."""

    def __init__(self, errors):
        self.errors = errors


async def _get_form(request):
    """Parse the request's form/multipart body once (spooling large uploads to
    disk via Starlette). Delegates to the shared Request helper."""
    return await request._parsed_form()


def _form_value(form, spec):
    """Pull a Form()/File() marker's raw value from parsed form data."""
    if spec.location == "file":
        files = [v for v in form.getlist(spec.lookup) if not isinstance(v, str)]
        if spec.is_sequence:
            return files if files else ...
        return files[0] if files else ...
    if spec.is_sequence:
        values = [v for v in form.getlist(spec.lookup) if isinstance(v, str)]
        return values if values else ...
    value = form.get(spec.lookup)
    return value if isinstance(value, str) else ...


async def _resolve_markers(
    view: Callable, request: Request, path_params: dict[str, Any]
) -> tuple[dict, set]:
    """Validate a view's Query/Header/Cookie/Path/Form/File markers into kwargs.

    Returns ``({param: value}, drop_keys)`` where ``drop_keys`` are path-param
    names a renamed ``Path`` marker consumed (so the raw URL key doesn't leak as
    an unexpected kwarg); raises :class:`_MarkerValidationError` on any
    validation failure. Works for function views and CBV methods alike.
    """
    from .params import marker_params, raw_value

    specs = marker_params(view, _view_type_hints(view))
    if not specs:
        return {}, set()
    form = (
        await _get_form(request)
        if any(s.location in ("form", "file") for s in specs)
        else None
    )
    values: dict[str, Any] = {}
    drop: set = set()
    errors: list[dict] = []
    for spec in specs:
        if spec.location == "path" and spec.lookup != spec.name:
            drop.add(spec.lookup)
        if spec.name in path_params and spec.location != "path":
            continue  # path parameter wins over a marker of the same name
        if spec.location in ("form", "file"):
            raw = _form_value(form, spec)
        else:
            raw = raw_value(spec, request, path_params)
        if raw is ...:
            if spec.required:
                errors.append(
                    {
                        "loc": [spec.location, spec.lookup],
                        "msg": "field required",
                        "type": "missing",
                    }
                )
            else:
                values[spec.name] = spec.marker.default
            continue
        if spec.adapter is None:
            values[spec.name] = raw
            continue
        try:
            values[spec.name] = spec.adapter.validate_python(raw)
        except Exception as exc:
            if hasattr(exc, "errors"):
                for err in exc.errors():
                    err = dict(err)
                    err["loc"] = [spec.location, spec.lookup]
                    errors.append(err)
            else:
                errors.append(
                    {"loc": [spec.location, spec.lookup], "msg": str(exc)}
                )
    if errors:
        raise _MarkerValidationError(errors)
    return values, drop


_ASYNC_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _is_async(fn: Callable) -> bool:
    """Whether ``fn`` (or its ``__call__``) is a coroutine function, cached.

    A view/hook's async-ness is fixed at definition time, so this memoizes the
    (comparatively costly) ``inspect`` check off the per-request hot path.
    """
    key = getattr(fn, "__func__", fn)
    try:
        return _ASYNC_CACHE[key]
    except (KeyError, TypeError):
        pass
    result = inspect.iscoroutinefunction(fn) or inspect.iscoroutinefunction(
        getattr(fn, "__call__", None)  # noqa: B004 - inspecting __call__, not testing callability
    )
    try:
        _ASYNC_CACHE[key] = result
    except TypeError:
        pass
    return result


async def _invoke_provider(
    provider: Callable, kwargs: dict
) -> tuple[Any, Callable | None]:
    """Call a provider with pre-resolved kwargs, returning ``(value, teardown)``.

    Providers may be sync/async functions or sync/async generators (code after
    ``yield`` runs as teardown), including callable instances whose ``__call__``
    is a generator. Sub-dependencies and the request are passed in via
    ``kwargs`` by the resolver.
    """
    # For a callable instance, the generator-ness lives on __call__, not the
    # object itself; inspect that (calling provider(**kwargs) still dispatches
    # to __call__). For a plain function/method, inspect it directly.
    target = provider
    if not (inspect.isfunction(provider) or inspect.ismethod(provider)):
        target = getattr(provider, "__call__", provider)  # noqa: B004 - inspecting __call__, not testing callability

    if inspect.isasyncgenfunction(target):
        agen = provider(**kwargs)
        value = await agen.__anext__()

        async def teardown_async():
            try:
                await agen.__anext__()
            except StopAsyncIteration:
                pass

        return value, teardown_async

    if inspect.isgeneratorfunction(target):
        gen = provider(**kwargs)
        value = await run_in_threadpool(next, gen)

        async def teardown_sync():
            await run_in_threadpool(lambda: next(gen, None))

        return value, teardown_sync

    # ``_is_async`` (unlike ``iscoroutinefunction``) also detects a callable
    # instance whose ``__call__`` is async — e.g. an auth scheme used as a
    # dependency — so those are awaited rather than run in a thread.
    if _is_async(provider):
        return await provider(**kwargs), None

    return await run_in_threadpool(provider, **kwargs), None


class _RequestResolver:
    """Resolves a request's dependency graph: recursive sub-dependencies,
    whole-graph memoization, cycle detection, and reverse-topological teardown.
    """

    __slots__ = (
        "registry",
        "app_deps",
        "request",
        "req_names",
        "override_names",
        "cache",
        "provider_cache",
        "teardowns",
        "stack",
    )

    def __init__(
        self, registry, app_deps, request, req_names, override_names=frozenset()
    ):
        self.registry = registry
        self.app_deps = app_deps
        self.request = request
        self.req_names = req_names
        self.override_names = override_names
        self.cache: dict[str, Any] = {}
        self.provider_cache: dict[Any, Any] = {}
        self.teardowns: list[Callable] = []
        self.stack: list[str] = []

    def _depends_on_override(self, name, seen=None):
        """Whether app-dep ``name`` transitively depends on an overridden dep.

        Such an app-dep must be resolved request-scoped (not served from — or
        written to — the app cache), so a ``dependency_overrides`` block reaches
        deep into the app-scoped graph and restores cleanly afterward.
        """
        if not self.override_names:
            return False
        if seen is None:
            seen = set()
        if name in seen:
            return False
        seen.add(name)
        if name in self.override_names:
            return True
        provider, _scope = self.registry[name]
        for pname, _ann in _dep_param_specs(provider):
            if pname in self.registry and self._depends_on_override(pname, seen):
                return True
        return False

    async def resolve(self, name):
        if name in self.cache:  # whole-graph memo
            return self.cache[name]
        provider, scope = self.registry[name]
        if scope == "app" and not self._depends_on_override(name):
            value = await self.app_deps.resolve(name, self.registry)
            self.cache[name] = value
            return value
        if name in self.stack:
            path = self.stack[self.stack.index(name) :] + [name]
            raise DependencyCycleError("Dependency cycle: " + " -> ".join(path))
        self.stack.append(name)
        try:
            kwargs: dict[str, Any] = {}
            specs = _dep_param_specs(provider)
            depends = _depends_params(provider)
            for pname, ann in specs:
                if _is_request_param(pname, ann, self.req_names):
                    kwargs[pname] = self.request
                elif pname in depends:
                    kwargs[pname] = await self.resolve_provider(depends[pname].provider)
                elif pname in self.registry:
                    kwargs[pname] = await self.resolve(pname)
                else:
                    chain = " -> ".join(self.stack)
                    raise DependencyResolutionError(
                        f"Parameter {pname!r} of dependency {name!r} is neither the "
                        f"request (name it 'req' / annotate 'Request'), a registered "
                        f"dependency, nor an inline Depends(...). "
                        f"Dependency chain: {chain}."
                    )
        finally:
            self.stack.pop()
        value, teardown = await _invoke_provider(provider, kwargs)
        self.cache[name] = value
        if teardown is not None:
            self.teardowns.append(teardown)
        return value

    async def resolve_provider(self, provider: Callable) -> Any:
        key = provider
        try:
            return self.provider_cache[key]
        except (KeyError, TypeError):
            pass
        label = getattr(provider, "__name__", provider.__class__.__name__)
        if label in self.stack:
            path = self.stack[self.stack.index(label) :] + [label]
            raise DependencyCycleError("Dependency cycle: " + " -> ".join(path))
        self.stack.append(label)
        try:
            kwargs: dict[str, Any] = {}
            depends = _depends_params(provider)
            for pname, ann in _dep_param_specs(provider):
                if _is_request_param(pname, ann, self.req_names):
                    kwargs[pname] = self.request
                elif pname in depends:
                    kwargs[pname] = await self.resolve_provider(depends[pname].provider)
                elif pname in self.registry:
                    kwargs[pname] = await self.resolve(pname)
                else:
                    chain = " -> ".join(self.stack)
                    raise DependencyResolutionError(
                        f"Parameter {pname!r} of dependency provider {label!r} "
                        "is neither the request, a registered dependency, nor an "
                        f"inline Depends(...). Dependency chain: {chain}."
                    )
        finally:
            self.stack.pop()
        value, teardown = await _invoke_provider(provider, kwargs)
        try:
            self.provider_cache[key] = value
        except TypeError:
            pass
        if teardown is not None:
            self.teardowns.append(teardown)
        return value

    async def teardown(self):
        for td in reversed(self.teardowns):
            try:
                await td()
            except Exception:
                logger.exception("Dependency teardown failed")


def _accepts_json(scope: Scope) -> bool:
    """Whether the request's Accept header asks for JSON."""
    for key, value in scope.get("headers", []):
        if key == b"accept":
            return b"json" in value
    return False


def _validation_errors(exc: Any) -> list[dict] | None:
    """Extract structured validation errors from an exception if available."""
    error_values = getattr(exc, "errors", None)
    if error_values is None:
        return None
    if callable(error_values):
        try:
            error_values = error_values()
        except TypeError:
            return None
    if error_values is None:
        return None
    if isinstance(error_values, tuple):
        return list(error_values)
    if isinstance(error_values, list):
        return error_values
    return [{"msg": str(error_values)}]


def _error_payload(scope, status_code, detail=None, *, title=None, errors=None):
    if scope.get("problem_details"):
        return problem_payload_for(
            scope, status_code, detail, title=title, errors=errors
        )
    return legacy_error_payload(status_code, detail, title=title, errors=errors)


def _trace(scope: Scope, stage: str, **values: Any) -> None:
    if not scope.get("trace_dispatch"):
        return
    route = scope.get("route_pattern") or scope.get("path")
    bits = " ".join(f"{key}={value!r}" for key, value in values.items())
    logger.debug("dispatch.%s %s %s", stage, route, bits)


def _callable_label(fn: Callable) -> str:
    return getattr(fn, "__name__", fn.__class__.__name__)


def _fresh_child_scope(child_scope: dict) -> dict:
    return {k: dict(v) if isinstance(v, dict) else v for k, v in child_scope.items()}


class BaseRoute:
    route: str
    endpoint: Callable

    def url(self, **params: Any) -> str:
        raise NotImplementedError()

    def matches(self, scope: Scope) -> tuple[bool, dict]:
        raise NotImplementedError()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        raise NotImplementedError()

    async def _dispatch_hook(self, hook: Callable, *args: Any) -> None:
        """Invoke a hook, awaiting coroutines and offloading sync callables.

        Shared by the HTTP and websocket before/after hook runners; each caller
        layers its own short-circuit and error-handling policy around it.
        """
        if _is_async(hook):
            await hook(*args)
        else:
            await run_in_threadpool(hook, *args)

    async def _route_auth_injections(
        self, request: Request | WebSocket
    ) -> dict[str, Any]:
        route_auth = getattr(self.endpoint, "_route_auth", ())
        if not route_auth:
            return {}

        principals = []
        for auth in route_auth:
            if hasattr(auth, "authenticate"):
                principal = await auth.authenticate(request)
            elif _is_async(auth):
                principal = await auth(request)
            else:
                principal = await run_in_threadpool(auth, request)
            if principal is not None or getattr(auth, "optional_auth", False):
                principals.append(principal)

        value = principals[0] if len(principals) == 1 else principals
        request.state.auth = value
        request.state.user = value
        return {"auth": value, "principal": value, "user": value}

    async def _run_route_dependencies(self, resolver: _RequestResolver) -> None:
        for dependency in getattr(self.endpoint, "_route_dependencies", ()):
            await resolver.resolve_provider(dependency.provider)


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
        name: str | None = None,
    ) -> None:
        assert route.startswith("/"), "Route path must start with '/'"
        self.route = route
        self.endpoint = endpoint
        self.before_request = before_request
        self.name = name
        self.methods: set[str] | None = {m.upper() for m in methods} if methods else None

        self.path_re: re.Pattern
        self.param_convertors: dict[str, type]
        self.param_convertor_names: dict[str, str]
        (
            self.path_re,
            self.param_convertors,
            self.param_convertor_names,
        ) = compile_path(route)
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

    def _exchange(self, scope: Scope, receive: Receive) -> tuple[Request, Response]:
        formats = scope.get("formats") or get_formats()
        request = Request(scope, receive, api=scope.get("api"), formats=formats)
        response = Response(
            req=request,
            formats=formats,
            auto_etag=scope.get("auto_etag", False),
            auto_vary=scope.get("auto_vary", False),
        )
        return request, response

    def _problem_content_type(self, scope: Scope, response: Response) -> None:
        if scope.get("problem_details"):
            response.mimetype = PROBLEM_JSON
            response.headers["Content-Type"] = PROBLEM_JSON

    def _set_error_response(
        self,
        scope: Scope,
        response: Response,
        status_code: int,
        detail: str | None = None,
        *,
        title: str | None = None,
        errors: list[dict] | None = None,
        exc: Exception | None = None,
    ) -> None:
        response.reset_for_error()
        if scope.get("problem_details"):
            response.content = problem_bytes_for(
                scope,
                status_code,
                detail,
                title=title,
                errors=errors,
                request=response.req,
                exc=exc,
            )
            self._problem_content_type(scope, response)
        else:
            response.media = _error_payload(
                scope, status_code, detail, title=title, errors=errors
            )

    async def _send_validation_error(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        response: Response,
        exc: Exception,
    ) -> None:
        response.status_code = 422
        errors = _validation_errors(exc)
        if errors is None:
            errors = [{"msg": str(exc)}]
        self._set_error_response(
            scope,
            response,
            422,
            "Validation failed",
            title="Validation Error",
            errors=errors,
            exc=exc,
        )
        await response(scope, receive, send)

    async def _run_hooks(
        self, hooks: Iterable[Callable], request: Request, response: Response
    ) -> bool:
        for hook in hooks:
            _trace(request._starlette.scope, "before_hook", hook=_callable_label(hook))
            args = (request, response) if _accepts_arg_count(hook, 2) else (request,)
            await self._dispatch_hook(hook, *args)
            if response.status_code is not None:
                return False
        return True

    async def _validate_params_model(self, request: Request) -> None:
        params_model = getattr(self.endpoint, "_params_model", None)
        if params_model is None:
            return
        data = {}
        for key in request.params:
            values = request.params.get_list(key)
            data[key] = values if len(values) > 1 else values[-1]
        request.state.validated_params = params_model(**data)

    async def _body_injections(
        self, scope: Scope, request: Request, path_params: dict
    ) -> dict[str, Any]:
        if inspect.isclass(self.endpoint) or request.method not in (
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        ):
            return {}

        candidates = _body_model_candidates(self.endpoint)
        if not candidates:
            return {}
        dep_names = scope.get("dependencies") or {}
        auth_names = (
            _AUTH_INJECTION_NAMES
            if getattr(self.endpoint, "_route_auth", ())
            else frozenset()
        )
        model_params = [
            (name, model)
            for name, model in candidates
            if name not in path_params
            and name not in dep_names
            and name not in auth_names
        ]
        if not model_params:
            return {}

        body = await request.media()
        if not isinstance(body, dict):
            raise TypeError("Request body must be a JSON object")
        return {name: model.model_validate(body) for name, model in model_params}

    async def _validate_inputs(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        request: Request,
        response: Response,
        path_params: dict,
    ) -> tuple[bool, dict[str, Any]]:
        try:
            await self._validate_params_model(request)
            injected = await self._body_injections(scope, request, path_params)
        except HTTPException:
            raise
        except Exception as exc:
            await self._send_validation_error(scope, receive, send, response, exc)
            return False, {}
        return True, injected

    def _views_for(self, request: Request) -> list[Callable]:
        if not inspect.isclass(self.endpoint):
            return [self.endpoint]

        endpoint = self.endpoint()
        views = []
        on_request = getattr(endpoint, "on_request", None)
        if on_request:
            views.append(on_request)

        method_name = f"on_{request.method.lower()}"
        try:
            views.append(getattr(endpoint, method_name))
        except AttributeError as ex:
            if on_request is None:
                raise HTTPException(status_code=status_codes.HTTP_405) from ex
        return views

    async def _view_kwargs(
        self,
        view: Callable,
        request: Request,
        resolver: _RequestResolver,
        path_params: dict,
        injected: dict[str, Any],
        auth_injected: dict[str, Any],
    ) -> dict[str, Any]:
        kwargs = dict(path_params)
        kwargs.update(
            _coerce_typed_path_params(view, path_params, self.param_convertor_names)
        )
        if injected and view is self.endpoint:
            kwargs.update(injected)

        marker_values, drop_keys = await _resolve_markers(view, request, path_params)
        for key in drop_keys:
            kwargs.pop(key, None)
        kwargs.update(marker_values)

        depends_params = _depends_params(view)
        dependencies = resolver.registry
        for name in _view_param_names(view):
            if name in kwargs:
                continue
            if name in depends_params:
                kwargs[name] = await resolver.resolve_provider(
                    depends_params[name].provider
                )
            elif name in dependencies:
                kwargs[name] = await resolver.resolve(name)
            elif name in auth_injected:
                kwargs[name] = auth_injected[name]
        return kwargs

    async def _invoke_view(
        self, view: Callable, request: Request, response: Response, kwargs: dict
    ) -> Any:
        if _is_async(view):
            return await view(request, response, **kwargs)
        return await run_in_threadpool(view, request, response, **kwargs)

    def _apply_result(self, response: Response, result: Any) -> None:
        if result is None:
            return
        if isinstance(result, tuple):
            body, *rest = result
            if rest:
                response.status_code = rest[0]
            if len(rest) > 1 and rest[1]:
                response.headers.update(rest[1])
            result = body
        if isinstance(result, (dict, list)):
            response.media = result
        elif isinstance(result, str):
            response.text = result
        elif isinstance(result, bytes):
            response.content = result
        elif hasattr(result, "model_dump") or (
            dataclasses.is_dataclass(result) and not isinstance(result, type)
        ):
            response.media = result

    async def _run_views(
        self,
        views: list[Callable],
        request: Request,
        response: Response,
        resolver: _RequestResolver,
        path_params: dict,
        injected: dict[str, Any],
        auth_injected: dict[str, Any],
    ) -> None:
        for view in views:
            _trace(request._starlette.scope, "handler", view=_callable_label(view))
            kwargs = await self._view_kwargs(
                view, request, resolver, path_params, injected, auth_injected
            )
            result = await self._invoke_view(view, request, response, kwargs)
            self._apply_result(response, result)

    def _response_model(self) -> tuple[Any, bool]:
        resp_model = getattr(self.endpoint, "_response_model", None)
        explicit_model = resp_model is not None
        if resp_model is None and not inspect.isclass(self.endpoint):
            return_hint = _view_type_hints(self.endpoint).get("return")
            if _is_pydantic_model(return_hint):
                resp_model = return_hint
        return resp_model, explicit_model

    def _response_model_failure(
        self, scope: Scope, response: Response, exc: Exception | None = None
    ) -> None:
        response.status_code = 500
        self._set_error_response(
            scope,
            response,
            500,
            INTERNAL_SERVER_ERROR,
            errors=_validation_errors(exc) if exc is not None else None,
            exc=exc,
        )

    def _validate_response_model(self, scope: Scope, response: Response) -> None:
        resp_model, explicit_model = self._response_model()
        if (
            resp_model is not None
            and _is_pydantic_model(resp_model)
            and (
                isinstance(response.media, dict)
                or hasattr(response.media, "model_dump")
            )
        ):
            try:
                response.media = resp_model.model_validate(
                    response.media
                ).model_dump(mode="json")
            except Exception as exc:
                logger.exception("response_model validation failed")
                if getattr(scope.get("api"), "debug", False):
                    raise
                self._response_model_failure(scope, response, exc)
        elif (
            explicit_model
            and not _is_pydantic_model(resp_model)
            and response.media is not None
        ):
            try:
                adapter = _response_type_adapter(resp_model)
                response.media = adapter.dump_python(
                    adapter.validate_python(response.media), mode="json"
                )
            except Exception as exc:
                logger.exception("response_model validation failed")
                if getattr(scope.get("api"), "debug", False):
                    raise
                self._response_model_failure(scope, response, exc)

    async def _send_timeout_response(
        self, scope: Scope, receive: Receive, send: Send, response: Response
    ) -> None:
        response.status_code = 504
        if scope.get("problem_details"):
            self._set_error_response(scope, response, 504, "Request timed out")
        elif _accepts_json(scope):
            response.media = _error_payload(scope, 504, "Request timed out")
        else:
            response.text = "Request timed out"
        await response(scope, receive, send)

    async def _run_after_hooks(
        self, scope: Scope, request: Request, response: Response
    ) -> None:
        route_after = getattr(self.endpoint, "_route_after", ())
        after_requests = scope.get("after_requests", [])
        for hook in (*route_after, *after_requests):
            _trace(scope, "after_hook", hook=_callable_label(hook))
            args = (request, response) if _accepts_arg_count(hook, 2) else (request,)
            try:
                await self._dispatch_hook(hook, *args)
            except Exception as exc:
                logger.exception("after_request hook failed")
                if getattr(scope.get("api"), "debug", False):
                    raise
                response.status_code = 500
                self._set_error_response(
                    scope, response, 500, INTERNAL_SERVER_ERROR, exc=exc
                )
                return

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request, response = self._exchange(scope, receive)
        path_params = scope.get("path_params", {})
        before_requests = scope.get("before_requests", {"http": [], "ws": []})
        route_before = getattr(self.endpoint, "_route_before", ())

        if not await self._run_hooks(
            (*before_requests.get("http", []), *route_before), request, response
        ):
            await response(scope, receive, send)
            return

        _trace(scope, "auth")
        auth_injected = await self._route_auth_injections(request)
        ok, injected = await self._validate_inputs(
            scope, receive, send, request, response, path_params
        )
        if not ok:
            return

        views = self._views_for(request)
        dependencies = scope.get("dependencies") or {}
        resolver = _RequestResolver(
            dependencies,
            scope.get("app_dependencies"),
            request,
            _HTTP_REQUEST_NAMES,
            scope.get("dependency_override_names", frozenset()),
        )
        try:
            try:
                _trace(scope, "dependencies")
                await self._run_route_dependencies(resolver)
                run = self._run_views(
                    views,
                    request,
                    response,
                    resolver,
                    path_params,
                    injected,
                    auth_injected,
                )
                timeout = scope.get("request_timeout")
                if timeout:
                    await asyncio.wait_for(run, timeout)
                else:
                    await run
            except asyncio.TimeoutError:
                await self._send_timeout_response(scope, receive, send, response)
                return
            except _MarkerValidationError as exc:
                await self._send_validation_error(scope, receive, send, response, exc)
                return

            self._validate_response_model(scope, response)
            await self._run_after_hooks(scope, request, response)
            if response.status_code is None:
                response.status_code = status_codes.HTTP_200
            await response(scope, receive, send)
        finally:
            await resolver.teardown()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Route):
            return NotImplemented
        return self.route == other.route and self.endpoint == other.endpoint

    def __hash__(self) -> int:
        # Mirror __eq__ (route + endpoint) so equal routes hash equal.
        return hash(self.route) ^ hash(self.endpoint)


class WebSocketRoute(BaseRoute):
    """A WebSocket route that maps a URL pattern to a WebSocket handler."""

    def __init__(
        self,
        route: str,
        endpoint: Callable,
        *,
        before_request: bool = False,
        name: str | None = None,
    ) -> None:
        assert route.startswith("/"), "Route path must start with '/'"
        self.route = route
        self.endpoint = endpoint
        self.before_request = before_request
        self.name = name

        self.path_re: re.Pattern
        self.param_convertors: dict[str, type]
        self.param_convertor_names: dict[str, str]
        (
            self.path_re,
            self.param_convertors,
            self.param_convertor_names,
        ) = compile_path(route)
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

        idle_timeout = scope.get("ws_idle_timeout")
        if idle_timeout is not None:
            self._apply_idle_timeout(ws, idle_timeout)

        before_requests = scope.get("before_requests", {"http": [], "ws": []})
        route_before = getattr(self.endpoint, "_route_before", ())
        route_after = getattr(self.endpoint, "_route_after", ())
        resolver = None

        try:
            if not await self._run_before_hooks(
                (*before_requests.get("ws", []), *route_before), ws
            ):
                return

            auth_injected = await self._route_auth_injections(ws)
            if WebSocketState.DISCONNECTED in (ws.client_state, ws.application_state):
                return

            path_params = scope.get("path_params", {})
            dependencies = scope.get("dependencies") or {}
            app_deps = scope.get("app_dependencies")
            override_names = scope.get("dependency_override_names", frozenset())
            resolver = _RequestResolver(
                dependencies, app_deps, ws, _WS_REQUEST_NAMES, override_names
            )

            await self._run_route_dependencies(resolver)
            param_names = tuple(_view_param_names(self.endpoint, skip=1))
            kwargs = {
                name: path_params[name] for name in param_names if name in path_params
            }
            kwargs.update(
                {
                    name: value
                    for name, value in _coerce_typed_path_params(
                        self.endpoint, path_params, self.param_convertor_names
                    ).items()
                    if name in param_names
                }
            )
            depends_params = _depends_params(self.endpoint)
            for name in param_names:
                if name in kwargs:
                    continue
                if name in depends_params:
                    kwargs[name] = await resolver.resolve_provider(
                        depends_params[name].provider
                    )
                elif name in dependencies:
                    kwargs[name] = await resolver.resolve(name)
                elif name in auth_injected:
                    kwargs[name] = auth_injected[name]

            await self.endpoint(ws, **kwargs)
            await self._run_after_hooks(route_after, ws)
        except HTTPException:
            await self._close_if_connected(ws, code=1008)
        except _MarkerValidationError:
            await self._close_if_connected(ws, code=1008)
        except TimeoutError:
            # No inbound message arrived within ws_idle_timeout; close the
            # idle connection with 1001 (going away) instead of hanging.
            await self._close_if_connected(ws, code=1001)
        finally:
            if resolver is not None:
                await resolver.teardown()

    @staticmethod
    def _apply_idle_timeout(ws: WebSocket, timeout: float) -> None:
        """Shadow ``ws.receive`` so each awaited receive must resolve within
        ``timeout`` seconds. The deadline resets on every message, so it bounds
        idle time between messages, not the total connection lifetime. On expiry
        ``asyncio.wait_for`` raises ``TimeoutError``, handled in ``__call__``.
        """
        original_receive = ws.receive

        async def receive_with_timeout() -> Any:
            return await asyncio.wait_for(original_receive(), timeout)

        ws.receive = receive_with_timeout  # type: ignore[method-assign]

    async def _run_before_hooks(
        self, hooks: Iterable[Callable], ws: WebSocket
    ) -> bool:
        for hook in hooks:
            await self._dispatch_hook(hook, ws)
            # If a hook closed the connection, short-circuit the endpoint.
            if WebSocketState.DISCONNECTED in (ws.client_state, ws.application_state):
                return False
        return True

    async def _run_after_hooks(
        self, hooks: Iterable[Callable], ws: WebSocket
    ) -> None:
        for hook in hooks:
            try:
                await self._dispatch_hook(hook, ws)
            except Exception:
                # A failing after-hook must not escape into the ASGI task and
                # crash the (often already-closed) websocket. Log it and move
                # on, re-raising only under debug for visibility.
                logger.exception("websocket after_request hook failed")
                if getattr(ws.scope.get("api"), "debug", False):
                    raise

    async def _close_if_connected(self, ws: WebSocket, *, code: int) -> None:
        if WebSocketState.DISCONNECTED not in (
            ws.client_state,
            ws.application_state,
        ):
            await ws.close(code=code)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, WebSocketRoute):
            return NotImplemented
        return self.route == other.route and self.endpoint == other.endpoint

    def __hash__(self) -> int:
        # Mirror __eq__ (route + endpoint) so equal routes hash equal.
        return hash(self.route) ^ hash(self.endpoint)


class _AppDependencyState:
    """Holds app-scoped dependency values for the lifetime of the application."""

    __slots__ = ("cache", "lock", "teardowns")

    def __init__(self) -> None:
        self.cache: dict[str, Any] = {}
        self.lock = asyncio.Lock()
        self.teardowns: list[Callable] = []

    async def resolve(self, name: str, registry: dict[str, Any]) -> Any:
        if name in self.cache:
            return self.cache[name]
        async with self.lock:
            return await self._resolve_locked(name, registry, [])

    async def _resolve_locked(
        self, name: str, registry: dict[str, Any], stack: list[str]
    ) -> Any:
        # Runs with self.lock held; recurses via this method (never re-acquires
        # the non-reentrant lock), so app-dependency graphs can't deadlock.
        if name in self.cache:
            return self.cache[name]
        if name in stack:
            path = stack[stack.index(name) :] + [name]
            raise DependencyCycleError("App dependency cycle: " + " -> ".join(path))
        provider, _scope = registry[name]
        stack.append(name)
        try:
            kwargs: dict[str, Any] = {}
            for pname, ann in _dep_param_specs(provider):
                if _is_request_param(
                    pname, ann, _WS_REQUEST_NAMES | _HTTP_REQUEST_NAMES
                ):
                    raise DependencyScopeError(
                        f"App-scoped dependency {name!r} cannot receive the request."
                    )
                if pname not in registry:
                    raise DependencyResolutionError(
                        f"App-scoped dependency {name!r}: unknown parameter {pname!r}."
                    )
                if registry[pname][1] != "app":
                    raise DependencyScopeError(
                        f"App-scoped dependency {name!r} cannot depend on "
                        f"request-scoped dependency {pname!r}."
                    )
                kwargs[pname] = await self._resolve_locked(pname, registry, stack)
        finally:
            stack.pop()
        value, teardown = await _invoke_provider(provider, kwargs)
        self.cache[name] = value
        if teardown is not None:
            self.teardowns.append(teardown)
        return value

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
        auto_vary: bool = False,
        request_timeout: float | None = None,
        ws_idle_timeout: float | None = None,
        trace_dispatch: bool = False,
        problem_details: bool = True,
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
        # Test-time overrides: same registry shape, always request-scoped so
        # they take precedence over (and bypass the cache of) any real dep.
        self.dependency_overrides: dict[str, tuple[Callable, str]] = {}
        self.app_dependencies = _AppDependencyState()
        self.api: Any = None  # Set by API.__init__; reaches views as req.api.
        self.redirect_slashes = redirect_slashes
        self.max_request_size = max_request_size
        self.auto_etag = auto_etag
        self.auto_vary = auto_vary
        self.request_timeout = request_timeout
        self.ws_idle_timeout = ws_idle_timeout
        self.trace_dispatch = trace_dispatch
        self.problem_details = problem_details
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
        name: str | None = None,
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

        if check_existing:
            new_methods = {m.upper() for m in methods} if methods else None
            for item in self.routes:
                if item.route != route:
                    continue
                # Same path is allowed only for HTTP routes whose methods are
                # disjoint — e.g. @api.get and @api.post on one path. A
                # method-less route (or a WebSocket route) answers
                # unconditionally, so it always conflicts.
                existing_methods = getattr(item, "methods", None)
                if (
                    not websocket
                    and isinstance(item, Route)
                    and new_methods is not None
                    and existing_methods is not None
                    and new_methods.isdisjoint(existing_methods)
                ):
                    continue
                raise ValueError(f"Route '{route}' already exists")

        if default:
            self.default_endpoint = endpoint

        new_route: BaseRoute
        if websocket:
            new_route = WebSocketRoute(route, endpoint, name=name)
        else:
            new_route = Route(route, endpoint, methods=methods, name=name)

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
                # Run blocking startup/shutdown handlers off the event loop.
                await run_in_threadpool(handler)

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
        if name in _RESERVED_DEP_NAMES:
            raise ValueError(
                f"Dependency name {name!r} is reserved (req/request/resp/response/"
                f"ws/websocket)."
            )
        if scope == "app":
            # App-scoped providers may depend on other (app-scoped) providers,
            # but never on the request — they outlive any single request.
            for pname, ann in _dep_param_specs(provider):
                if _is_request_param(
                    pname, ann, _WS_REQUEST_NAMES | _HTTP_REQUEST_NAMES
                ):
                    raise ValueError(
                        "App-scoped dependency providers cannot receive the "
                        "request — they outlive any single request."
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
        # An explicit route name wins (decouples reversal from function identity,
        # so lambdas and shared function names are addressable).
        if isinstance(endpoint, str):
            for route in self.routes:
                if getattr(route, "name", None) == endpoint:
                    return route.url(**params)
        for route in self.routes:
            if endpoint in (route.endpoint, route.endpoint.__name__):
                return route.url(**params)
        return None

    async def default_response(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            websocket_close = WebSocketClose()
            await websocket_close(scope, receive, send)
            return

        raise HTTPException(status_code=status_codes.HTTP_404)

    def _resolve_route(self, scope: Scope) -> BaseRoute | None:
        key = (scope.get("method", "ws"), scope["path"])
        cached = self._route_cache.get(key)
        if cached is not None:
            route, child_scope = cached
            # Copy path_params so per-request mutation can't poison the cache.
            scope.update(_fresh_child_scope(child_scope))
            scope["route_pattern"] = getattr(route, "path_template", route.route)
            return route

        for route in self.routes:
            matches, child_scope = route.matches(scope)
            if matches:
                scope.update(_fresh_child_scope(child_scope))
                scope["route_pattern"] = getattr(route, "path_template", route.route)
                if len(self._route_cache) >= 1024:
                    self._route_cache.clear()
                self._route_cache[key] = (route, _fresh_child_scope(child_scope))
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
        scope["dependencies"] = (
            {**self.dependencies, **self.dependency_overrides}
            if self.dependency_overrides
            else self.dependencies
        )
        scope["dependency_override_names"] = frozenset(self.dependency_overrides)
        scope["app_dependencies"] = self.app_dependencies
        scope["formats"] = self.formats
        scope["api"] = self.api
        scope["max_request_size"] = self.max_request_size
        scope["auto_etag"] = self.auto_etag
        scope["auto_vary"] = self.auto_vary
        scope["request_timeout"] = self.request_timeout
        scope["ws_idle_timeout"] = self.ws_idle_timeout
        scope["trace_dispatch"] = self.trace_dispatch
        scope["problem_details"] = self.problem_details

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
                elif self.problem_details or _accepts_json(scope):
                    content = _error_payload(scope, status_codes.HTTP_405)
                    media_type = (
                        PROBLEM_JSON
                        if self.problem_details
                        else "application/json"
                    )
                    response = JSONResponse(
                        content,
                        status_code=status_codes.HTTP_405,
                        headers=headers,
                        media_type=media_type,
                    )
                else:
                    response = StarletteResponse(
                        content="Method Not Allowed",
                        status_code=status_codes.HTTP_405,
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
