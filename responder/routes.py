from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import logging
import re
import sys
import traceback
import urllib.parse
import weakref
from collections import defaultdict
from collections.abc import (
    AsyncGenerator,
    AsyncIterable,
    Callable,
    Iterable,
    Iterator,
    Mapping,
)
from typing import TYPE_CHECKING, Any, Union, cast

if TYPE_CHECKING:
    from http.cookies import Morsel

__all__ = [
    "Route",
    "WebSocketRoute",
    "Router",
    "DependencyError",
    "DependencyCycleError",
    "DependencyScopeError",
    "DependencyResolutionError",
    "RouteNotFoundError",
]

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.responses import Response as StarletteResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import (
    WebSocket,
    WebSocketClose,
    WebSocketDisconnect,
    WebSocketState,
)

from . import status_codes
from .contracts import (
    inferred_response_model,
    response_body_kind,
    response_status_allows_body,
    response_type_adapter,
)
from .errors import (
    INTERNAL_SERVER_ERROR,
    PROBLEM_JSON,
    legacy_error_payload,
    problem_bytes_for,
    problem_payload_for,
)
from .formats import get_formats
from .models import Request, Response, _format_sse_event, _sse_with_heartbeat
from .params import _Depends
from .streaming import SSE

logger = logging.getLogger("responder")

_STREAM_END = object()
_STREAM_PENDING = object()


def _next_stream_item(iterator: Iterator[Any]) -> tuple[bool, Any]:
    """Read one sync iterator item without leaking StopIteration into asyncio."""
    try:
        return False, next(iterator)
    except StopIteration:
        return True, None


async def _iterate_stream_source(source: Any) -> AsyncGenerator[Any, None]:
    """Adapt sync and async iterables to one cancellation-safe async stream."""
    if isinstance(source, AsyncIterable):
        iterator = source.__aiter__()
        try:
            async for item in iterator:
                yield item
        finally:
            close = getattr(iterator, "aclose", None)
            if close is not None:
                await close()
        return

    iterator = iter(source)
    try:
        while True:
            done, item = await run_in_threadpool(_next_stream_item, iterator)
            if done:
                return
            yield item
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            await run_in_threadpool(close)


async def _close_stream_source(source: Any) -> None:
    """Close a not-yet-consumed stream source without starting it."""
    close = getattr(source, "aclose", None)
    if close is not None:
        await close()
        return
    close = getattr(source, "close", None)
    if close is not None:
        await run_in_threadpool(close)


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


class RouteNotFoundError(LookupError):
    """``url_for`` was asked to reverse an endpoint or route name that no
    registered route matches."""


_UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

_CONVERTORS = {
    "int": (int, r"\d+"),
    "str": (str, r"[^/]+"),
    "float": (float, r"\d+(?:\.\d+)?"),
    "path": (str, r".+"),
    "uuid": (str, _UUID_RE),
}

PARAM_RE = re.compile("{([a-zA-Z_][a-zA-Z0-9_]*)(:[a-zA-Z_][a-zA-Z0-9_]*)?}")

_KNOWN_HTTP_METHODS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"}
)


def _class_view_methods(endpoint: Any) -> set[str]:
    """HTTP methods a class-based view instance implements via ``on_*`` handlers,
    plus implicit HEAD-for-GET and OPTIONS.

    OPTIONS is always included: when a class defines no ``on_options`` handler
    (and no ``on_request``), CBV dispatch answers OPTIONS automatically with
    ``200`` + ``Allow`` — the same treatment method-restricted function routes
    get at the router level — so advertising it is accurate.
    """
    methods = {
        name[3:].upper()
        for name in dir(endpoint)
        if name.startswith("on_")
        and name[3:].upper() in _KNOWN_HTTP_METHODS
        and callable(getattr(endpoint, name, None))
    }
    if "GET" in methods:
        methods.add("HEAD")
    methods.add("OPTIONS")
    return methods


def _auto_options_view(allow: str) -> Callable:
    """A synthetic view answering ``OPTIONS`` with ``200`` + ``Allow`` for a
    class-based view that defines neither ``on_options`` nor ``on_request``,
    mirroring the router-level automatic OPTIONS response for
    method-restricted function routes."""

    def options_view(req: Request, resp: Response, **kwargs: Any) -> None:
        resp.status_code = status_codes.HTTP_200
        resp.headers["Allow"] = allow
        resp.content = b""

    return options_view


# Headers that frame a specific body; a replacement response builds its own
# (via ``Response.body``), so stale ones from the abandoned body must not leak.
_BODY_FRAMING_HEADERS = frozenset(
    {"content-type", "content-length", "content-range", "transfer-encoding"}
)


def _copy_response_metadata(source: Response, target: Response) -> None:
    """Copy user-set headers and cookies from ``source`` onto ``target``.

    Used when a timed-out request's response is rebuilt from scratch: metadata
    that completed before the view started (request-ID headers, CORS, cookies
    from before_request hooks) carries over, while headers framing the old
    body do not — ``Response`` keeps body-derived headers out of ``.headers``
    (they are computed from the body at render time), so the dict is copied
    wholesale minus the framing names a view may have set explicitly (e.g.
    ``resp.file()``'s Content-Length). Reads are a single snapshot: the
    abandoned view thread may keep mutating ``source`` afterwards, but
    ``target`` stays isolated.
    """
    for key, value in list(source.headers.items()):
        if key.lower() not in _BODY_FRAMING_HEADERS:
            target.headers[key] = value
    for key, morsel in list(source.cookies.items()):
        # Morsel.copy() returns a Morsel at runtime; typeshed types it as
        # the inherited dict.copy, hence the cast.
        target.cookies[key] = cast("Morsel[str]", morsel.copy())


def compile_path(path: str) -> tuple[re.Pattern, dict[str, type], dict[str, str]]:
    path_re = "^"
    param_convertors: dict[str, type] = {}
    param_convertor_names: dict[str, str] = {}
    idx = 0

    for match in PARAM_RE.finditer(path):
        param_name, convertor_type = match.groups(default="str")
        convertor_type = convertor_type.lstrip(":")
        if convertor_type not in _CONVERTORS:
            raise ValueError(
                f"Unknown path convertor {convertor_type!r} in route {path!r}. "
                f"Available convertors: {', '.join(sorted(_CONVERTORS))}."
            )
        if param_name in param_convertors:
            raise ValueError(
                f"Duplicate path parameter {param_name!r} in route {path!r}."
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
_VIEW_RETURN_HINT_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


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


def _view_return_hint(view: Callable) -> Any:
    """Resolved return hint with ``Annotated`` metadata preserved."""
    cache_key = getattr(view, "__func__", view)
    try:
        return _VIEW_RETURN_HINT_CACHE[cache_key]
    except (KeyError, TypeError):
        pass
    try:
        import typing

        hint = typing.get_type_hints(view, include_extras=True).get("return")
    except Exception:
        hint = _view_type_hints(view).get("return")
    try:
        _VIEW_RETURN_HINT_CACHE[cache_key] = hint
    except TypeError:
        pass
    return hint


def _is_pydantic_model(tp: Any) -> bool:
    """Whether ``tp`` is a Pydantic ``BaseModel`` subclass (duck-typed)."""
    return (
        isinstance(tp, type)
        and hasattr(tp, "model_validate")
        and hasattr(tp, "model_fields")
    )


_REQUEST_TYPES = (Request, WebSocket)
_HTTP_REQUEST_NAMES = frozenset({"req", "request"})
_WS_REQUEST_NAMES = frozenset({"ws", "websocket", "req", "request"})
_RESERVED_DEP_NAMES = frozenset({"req", "request", "resp", "response", "ws", "websocket"})
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
    parameters that have no default. Works for function views and (bound) CBV
    methods alike — the cache keys on ``__func__``, so per-request bound
    methods memoize correctly. Marker-driven parameters (e.g. an
    ``Annotated[Model, Form()]`` form model) are excluded; they are resolved
    by the marker pipeline instead. The per-request path/dependency/auth
    filtering is applied by the caller; only the signature/hint inspection is
    memoized here (it runs on every write request otherwise)."""
    key = getattr(endpoint, "__func__", endpoint)
    try:
        return _BODY_MODEL_CANDIDATES_CACHE[key]
    except (KeyError, TypeError):
        pass
    from .params import marker_params

    hints = _view_type_hints(endpoint)
    marker_names = {spec.name for spec in marker_params(endpoint, hints)}
    try:
        sig_params: Any = inspect.signature(endpoint).parameters
    except (TypeError, ValueError):
        sig_params = {}
    candidates = tuple(
        (name, hints[name])
        for name in _view_param_names(endpoint)
        if _is_pydantic_model(hints.get(name))
        and name not in marker_names
        and (
            name not in sig_params or sig_params[name].default is inspect.Parameter.empty
        )
    )
    try:
        _BODY_MODEL_CANDIDATES_CACHE[key] = candidates
    except TypeError:
        pass
    return candidates


def _is_wsgi_app(app: Any) -> bool:
    """Whether ``app`` looks like a WSGI (rather than ASGI) application.

    ASGI apps are coroutine functions, callables whose ``__call__`` is a
    coroutine, or expose ``__asgi_app__``; anything else with a two-positional
    call signature (``environ, start_response``) is treated as WSGI. Deciding
    this from the signature — instead of from the text of a ``TypeError`` raised
    while calling the app — means a genuine ``TypeError`` inside a mounted ASGI
    sub-app keeps its real traceback rather than being misclassified as WSGI.
    """
    if hasattr(app, "__asgi_app__") or not callable(app):
        return False
    # ASGI if the app (a function) or its bound ``__call__`` (an instance) is a
    # coroutine. ``inspect.signature`` resolves an instance to its ``__call__``
    # signature (self excluded), so a two-positional signature => WSGI.
    if inspect.iscoroutinefunction(app):
        return False
    if not inspect.isroutine(app) and inspect.iscoroutinefunction(app.__call__):
        return False
    try:
        positional = [
            p
            for p in inspect.signature(app).parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
    except (TypeError, ValueError):
        return False
    return len(positional) == 2


_ASGI_STYLE_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _is_asgi_style(fn: Callable) -> bool:
    """Whether ``fn`` has an ASGI call signature (``scope, receive, send``).

    Used to decide how a custom default endpoint is dispatched: ASGI-style
    callables (like the built-in :meth:`Router.default_response`) are invoked
    directly, while Responder ``(req, resp)`` views are dispatched through
    normal :class:`Route` semantics. Cached per underlying function.
    """
    if inspect.isclass(fn):
        return False
    key = getattr(fn, "__func__", fn)
    try:
        return _ASGI_STYLE_CACHE[key]
    except (KeyError, TypeError):
        pass
    if not _is_async(fn):
        result = False  # ASGI apps are always awaitable; sync => a view.
    else:
        try:
            params = inspect.signature(fn).parameters.values()
        except (TypeError, ValueError):
            result = True  # Uninspectable async callable: assume ASGI.
        else:
            positional = [
                p
                for p in params
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ]
            result = (
                any(p.kind == p.VAR_POSITIONAL for p in params) or len(positional) == 3
            )
    try:
        _ASGI_STYLE_CACHE[key] = result
    except TypeError:
        pass
    return result


def _quote_url_params(
    params: dict[str, Any], convertor_names: dict[str, str]
) -> dict[str, str]:
    """URL-quote path-parameter values for URL building (``Route.url``).

    ``{param:path}`` segments keep ``/`` unescaped; every other parameter value
    is fully percent-encoded (spaces become ``%20``, slashes ``%2F``, etc.).

    Note that ASGI servers percent-decode the path before route matching, so a
    non-path parameter value containing ``/`` cannot produce a matchable URL:
    the ``%2F`` decodes back to ``/`` and the default ``[^/]+`` segment pattern
    never matches it. Values without ``/`` (e.g. containing spaces) round-trip
    fine. Use a ``{param:path}`` convertor when values may contain slashes.
    """
    return {
        name: urllib.parse.quote(
            str(value), safe="/" if convertor_names.get(name) == "path" else ""
        )
        for name, value in params.items()
    }


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


def _form_model_data(form: Any, model: Any) -> dict[str, Any]:
    """Collect a Pydantic form model's raw field values from parsed form data.

    Text fields come through as strings (coerced by the model); fields whose
    submitted values are uploaded files receive the ``UploadFile`` objects
    directly. Sequence-typed fields collect every value sent under the field
    name; scalar fields take the last one. Fields absent from the form are
    omitted, so model defaults apply and missing required fields surface as
    Pydantic ``missing`` errors.
    """
    from .params import _is_sequence

    data: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        key = getattr(field, "alias", None) or name
        raw = form.getlist(key)
        if not raw:
            continue
        files = [v for v in raw if not isinstance(v, str)]
        chosen: list[Any] = files if files else list(raw)
        data[key] = chosen if _is_sequence(field.annotation) else chosen[-1]
    return data


def _validate_form_model(
    spec: Any, form: Any, values: dict[str, Any], errors: list[dict]
) -> None:
    """Bind an entire parsed form onto a Pydantic-model ``Form()`` parameter.

    Field-level failures are appended to ``errors`` with ``["form", field]``
    locations, matching the shape JSON body-model validation reports.
    """
    data = _form_model_data(form, spec.annotation) if form is not None else {}
    if not data and not spec.required:
        values[spec.name] = spec.marker.default
        return
    try:
        values[spec.name] = spec.annotation.model_validate(data)
    except Exception as exc:
        if hasattr(exc, "errors"):
            for err in exc.errors():
                err = dict(err)
                err["loc"] = ["form", *err.get("loc", ())]
                errors.append(err)
        else:
            errors.append({"loc": ["form", spec.lookup], "msg": str(exc)})


async def _resolve_markers(
    view: Callable, request: Request | WebSocket, path_params: dict[str, Any]
) -> tuple[dict, set]:
    """Validate a view's Query/Header/Cookie/Path/Form/File markers into kwargs.

    Returns ``({param: value}, drop_keys)`` where ``drop_keys`` are path-param
    names a renamed ``Path`` marker consumed (so the raw URL key doesn't leak as
    an unexpected kwarg); raises :class:`_MarkerValidationError` on any
    validation failure. Works for function views, CBV methods, and WebSocket
    handlers alike (a WebSocket has no body, so ``Form()``/``File()`` markers
    on a handler resolve as missing).
    """
    from .params import marker_params, raw_value

    specs = marker_params(view, _view_type_hints(view))
    if not specs:
        return {}, set()
    form = None
    if any(s.location in ("form", "file") for s in specs) and hasattr(
        request, "_parsed_form"
    ):
        form = await _get_form(request)
    values: dict[str, Any] = {}
    drop: set = set()
    errors: list[dict] = []
    for spec in specs:
        if spec.location == "path" and spec.lookup != spec.name:
            drop.add(spec.lookup)
        if spec.name in path_params and spec.location != "path":
            continue  # path parameter wins over a marker of the same name
        if spec.location == "form" and _is_pydantic_model(spec.annotation):
            _validate_form_model(spec, form, values, errors)
            continue
        if spec.location in ("form", "file"):
            raw = _form_value(form, spec) if form is not None else ...
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
                errors.append({"loc": [spec.location, spec.lookup], "msg": str(exc)})
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
        # Resolution stack of (identity, label) pairs. Cycle detection uses
        # the identity (registry name or provider object id) — never the bare
        # ``__name__``, which two distinct providers may share — while labels
        # keep error messages readable.
        self.stack: list[tuple[Any, str]] = []

    def _check_cycle(self, key: Any, label: str) -> None:
        keys = [k for k, _ in self.stack]
        if key in keys:
            path = [lbl for _, lbl in self.stack[keys.index(key) :]] + [label]
            raise DependencyCycleError("Dependency cycle: " + " -> ".join(path))

    def _chain(self) -> str:
        return " -> ".join(label for _, label in self.stack)

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
        key = ("dep", name)
        self._check_cycle(key, name)
        self.stack.append((key, name))
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
                    chain = self._chain()
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
        ident = ("provider", id(provider))
        self._check_cycle(ident, label)
        self.stack.append((ident, label))
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
                    chain = self._chain()
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
        return problem_payload_for(scope, status_code, detail, title=title, errors=errors)
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
    #: Per-route CSRF override, snapshotted at registration; ``None`` inherits
    #: the app-wide ``API(csrf=...)`` default.
    _csrf: bool | None = None

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
        if not route.startswith("/"):
            raise ValueError(f"Route path must start with '/', got {route!r}.")
        self.route = route
        self.endpoint = endpoint
        self.before_request = before_request
        self.name = name
        self.methods: set[str] | None = {m.upper() for m in methods} if methods else None
        self._response_models: dict[int, Any] = {}
        self._stream_mode: str | None = None
        self._stream_model: Any = None
        self._stream_heartbeat: float | None = None

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
        """The route's URL with ``params`` substituted (values URL-quoted;
        ``{param:path}`` segments keep their slashes)."""
        return self._url_template.format(
            **_quote_url_params(params, self.param_convertor_names)
        )

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
        self,
        scope: Scope,
        request: Request,
        path_params: dict,
        views: list[Callable],
    ) -> list[dict[str, Any]]:
        """Per-view Pydantic body-model injections, parallel to ``views``.

        The body is read and parsed at most once per request, and each
        ``(name, model)`` pair is validated once — so a class-based view whose
        ``on_request`` and ``on_post`` both declare the same model share a
        single validated instance.
        """
        injections: list[dict[str, Any]] = [{} for _ in views]
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return injections

        dep_names = scope.get("dependencies") or {}
        auth_names = (
            _AUTH_INJECTION_NAMES
            if getattr(self.endpoint, "_route_auth", ())
            else frozenset()
        )
        body: Any = None
        parsed = False
        validated: dict[tuple[str, Any], Any] = {}
        for index, view in enumerate(views):
            model_params = [
                (name, model)
                for name, model in _body_model_candidates(view)
                if name not in path_params
                and name not in dep_names
                and name not in auth_names
            ]
            if not model_params:
                continue
            if not parsed:
                body = await request.media()
                parsed = True
                if not isinstance(body, dict):
                    raise TypeError("Request body must be a JSON object")
            view_values: dict[str, Any] = {}
            for name, model in model_params:
                key = (name, model)
                if key not in validated:
                    validated[key] = model.model_validate(body)
                view_values[name] = validated[key]
            injections[index] = view_values
        return injections

    async def _validate_inputs(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        request: Request,
        response: Response,
        path_params: dict,
        views: list[Callable],
    ) -> tuple[bool, list[dict[str, Any]]]:
        try:
            await self._validate_params_model(request)
            injections = await self._body_injections(scope, request, path_params, views)
        except HTTPException:
            raise
        except Exception as exc:
            await self._send_validation_error(scope, receive, send, response, exc)
            return False, []
        return True, injections

    def _views_for(self, request: Request) -> list[Callable]:
        if not inspect.isclass(self.endpoint):
            return [self.endpoint]

        endpoint = self.endpoint()
        views = []
        on_request = getattr(endpoint, "on_request", None)
        if on_request:
            views.append(on_request)

        method_name = f"on_{request.method.lower()}"
        view = getattr(endpoint, method_name, None)
        if view is None and request.method.upper() == "HEAD":
            # HEAD is implicitly served by on_get, mirroring function views
            # (the response side already strips the body for HEAD).
            view = getattr(endpoint, "on_get", None)
        if view is not None:
            views.append(view)
        elif on_request is None:
            allow = ", ".join(sorted(_class_view_methods(endpoint)))
            if request.method.upper() == "OPTIONS":
                # No on_options handler: answer OPTIONS automatically with
                # 200 + Allow, mirroring the router-level treatment of
                # method-restricted function routes.
                return [_auto_options_view(allow)]
            # RFC 9110 §15.5.6: a 405 must list the methods the target
            # supports, mirroring the router-level function-route 405.
            raise HTTPException(
                status_code=status_codes.HTTP_405, headers={"Allow": allow}
            )
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
        if injected:
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
        if inspect.isasyncgenfunction(view) or inspect.isasyncgenfunction(
            getattr(view, "__call__", None)  # noqa: B004 - inspecting __call__
        ):
            return view(request, response, **kwargs)
        if _is_async(view):
            return await view(request, response, **kwargs)
        return await run_in_threadpool(view, request, response, **kwargs)

    def _typed_stream_item(self, response: Response, item: Any) -> Any:
        model = self._stream_model
        adapter = response_type_adapter(model)
        if self._stream_mode == "sse":
            metadata: dict[str, Any] = {}
            data = item
            if isinstance(item, SSE):
                if item.comment is not None:
                    if any(
                        value is not None
                        for value in (item.data, item.event, item.id, item.retry)
                    ):
                        raise ValueError(
                            "SSE(comment=...) must be a comment-only event"
                        )
                    if not isinstance(item.comment, str):
                        raise TypeError("SSE comment must be a string")
                    return {"comment": item.comment}
                data = item.data
                metadata = {
                    key: value
                    for key, value in {
                        "event": item.event,
                        "id": item.id,
                        "retry": item.retry,
                    }.items()
                    if value is not None
                }
                if item.event is not None and not isinstance(item.event, str):
                    raise TypeError("SSE event must be a string")
                if item.id is not None and (
                    isinstance(item.id, bool) or not isinstance(item.id, (str, int))
                ):
                    raise TypeError("SSE id must be a string or integer")
                if item.retry is not None and (
                    isinstance(item.retry, bool)
                    or not isinstance(item.retry, int)
                    or item.retry < 0
                ):
                    raise ValueError("SSE retry must be a non-negative integer")
            validated = adapter.validate_python(data)
            dumped = adapter.dump_python(validated, mode="json")
            return {"data": dumped, **metadata}

        validated = adapter.validate_python(item)
        return adapter.dump_json(validated) + b"\n"

    def _log_stream_contract_failure(
        self, scope: Scope, exc: Exception, view: Callable
    ) -> None:
        logger.error(
            "Stream contract failed for %s %s in %s; expected %r",
            scope.get("method", "HTTP"),
            scope.get("route_pattern", scope.get("path", "?")),
            _callable_label(view),
            self._stream_model,
            exc_info=exc,
        )

    def _set_typed_stream_headers(self, response: Response) -> None:
        if self._stream_mode == "sse":
            response.mimetype = "text/event-stream"
            response.headers["Cache-Control"] = "no-cache"
            response.headers["Connection"] = "keep-alive"
            response.headers["X-Accel-Buffering"] = "no"
        else:
            response.mimetype = "application/x-ndjson"

    async def _close_abandoned_typed_streams(
        self, response: Response, *, include_pending: bool = False
    ) -> None:
        sources = list(response._discarded_typed_streams)
        response._discarded_typed_streams = []
        if include_pending and response._typed_stream is not None:
            sources.append(response._typed_stream)
            response._typed_stream = None
        for source in sources:
            try:
                await _close_stream_source(source)
            except Exception:
                logger.exception("Failed to close an abandoned typed stream")

    async def _prepare_typed_stream(
        self, scope: Scope, response: Response, view: Callable
    ) -> None:
        if self._stream_mode is None:
            return
        await self._close_abandoned_typed_streams(response)
        status = response.status_code if response.status_code is not None else 200
        if status >= 400 or not response_status_allows_body(status):
            await self._close_abandoned_typed_streams(
                response, include_pending=True
            )
            return

        pending = response._typed_stream
        if pending is None:
            exc = TypeError(
                f"@api.{self._stream_mode} handler did not return an iterable"
            )
            if getattr(scope.get("api"), "debug", False):
                raise exc
            self._response_model_failure(
                scope, response, exc, model=self._stream_model, view=view
            )
            return

        source = pending
        response._typed_stream = None
        self._set_typed_stream_headers(response)
        iterator = _iterate_stream_source(source)

        if response.req.method == "HEAD":
            await _close_stream_source(source)

            async def empty_stream():
                if False:  # pragma: no cover - makes this an async generator
                    yield b""

            response._stream = empty_stream
            return

        first_task: asyncio.Future[Any] | None = None
        try:
            if self._stream_mode == "sse" and self._stream_heartbeat:
                first_task = asyncio.ensure_future(anext(iterator))
                # Let an immediately-yielding producer complete so its first
                # item can still fail before headers. A genuinely idle
                # producer remains pending and the heartbeat path starts.
                await asyncio.sleep(0)
                if first_task.done():
                    first = first_task.result()
                else:
                    first = _STREAM_PENDING
            else:
                first = await anext(iterator)
        except StopAsyncIteration:
            first = _STREAM_END
        except Exception as exc:
            await iterator.aclose()
            if getattr(scope.get("api"), "debug", False):
                raise
            self._response_model_failure(
                scope, response, exc, model=self._stream_model, view=view
            )
            return

        if first is not _STREAM_END and first is not _STREAM_PENDING:
            try:
                first = self._typed_stream_item(response, first)
            except Exception as exc:
                await iterator.aclose()
                if getattr(scope.get("api"), "debug", False):
                    raise
                self._response_model_failure(
                    scope, response, exc, model=self._stream_model, view=view
                )
                return

        async def validated_items():
            try:
                if first is _STREAM_PENDING:
                    assert first_task is not None
                    try:
                        pending_item = await first_task
                        yield self._typed_stream_item(response, pending_item)
                    except StopAsyncIteration:
                        return
                    except Exception as exc:
                        if getattr(scope.get("api"), "debug", False):
                            raise
                        self._log_stream_contract_failure(scope, exc, view)
                        return
                elif first is not _STREAM_END:
                    yield first
                async for item in iterator:
                    try:
                        yield self._typed_stream_item(response, item)
                    except Exception as exc:
                        if getattr(scope.get("api"), "debug", False):
                            raise
                        self._log_stream_contract_failure(scope, exc, view)
                        return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log_stream_contract_failure(scope, exc, view)
                if getattr(scope.get("api"), "debug", False):
                    raise
            finally:
                if first_task is not None and not first_task.done():
                    first_task.cancel()
                    with contextlib.suppress(BaseException):
                        await first_task
                await iterator.aclose()

        if self._stream_mode == "sse":

            async def body():
                events = validated_items()
                if self._stream_heartbeat:
                    events = _sse_with_heartbeat(events, self._stream_heartbeat)
                async for event in events:
                    yield _format_sse_event(event)

        else:
            body = validated_items
        response._stream = body

    def _apply_result(
        self,
        response: Response,
        result: Any,
        view: Callable,
    ) -> None:
        if result is None:
            return
        if isinstance(result, tuple):
            if len(result) not in (2, 3):
                raise TypeError(
                    "A returned tuple must be (body, status) or (body, status, headers)"
                )
            result, returned_status, *rest = result
            if (
                isinstance(returned_status, bool)
                or not isinstance(returned_status, int)
                or not 100 <= returned_status <= 599
            ):
                raise ValueError(
                    "The status in a returned tuple must be an integer "
                    f"from 100 through 599 (got {returned_status!r})"
                )
            response.status_code = returned_status
            if rest and rest[0] is not None:
                if not isinstance(rest[0], Mapping):
                    raise TypeError(
                        "The headers in a returned tuple must be a mapping or None"
                    )
                response.headers.update(rest[0])

        status = response.status_code if response.status_code is not None else 200
        if (
            self._stream_mode is not None
            and status < 400
        ):
            if not isinstance(result, (AsyncIterable, Iterable)) or isinstance(
                result, (str, bytes, bytearray, dict)
            ):
                raise TypeError(
                    f"@api.{self._stream_mode} handler must return a sync or "
                    "async iterable of typed items"
                )
            response._reset_body()
            response._typed_stream = result
            return
        response_model, explicit_response_model = self._response_model(view, status)
        if response_model is not None:
            kind = (
                "media" if explicit_response_model else response_body_kind(response_model)
            )
            if kind == "text":
                response.text = result
            elif kind == "bytes":
                response._reset_body()
                response.content = result
                response.mimetype = "application/octet-stream"
            else:
                response._reset_body()
                response.media = result
            return

        if isinstance(result, (dict, list)):
            response.media = result
        elif isinstance(result, str):
            response.text = result
        elif isinstance(result, bytes):
            response.content = result
        elif isinstance(result, (bool, int, float)):
            response.media = result
        elif hasattr(result, "model_dump") or (
            dataclasses.is_dataclass(result) and not isinstance(result, type)
        ):
            response.media = result
        else:
            raise TypeError(
                "Unsupported handler return value "
                f"{type(result).__name__}; mutate resp or return a JSON scalar, "
                "dict, list, str, bytes, model, dataclass, or "
                "(body, status[, headers]) tuple"
            )

    async def _run_views(
        self,
        views: list[Callable],
        request: Request,
        response: Response,
        resolver: _RequestResolver,
        path_params: dict,
        injections: list[dict[str, Any]],
        auth_injected: dict[str, Any],
    ) -> None:
        for view, injected in zip(views, injections, strict=True):
            _trace(request._starlette.scope, "handler", view=_callable_label(view))
            kwargs = await self._view_kwargs(
                view, request, resolver, path_params, injected, auth_injected
            )
            result = await self._invoke_view(view, request, response, kwargs)
            self._apply_result(response, result, view)

    def _response_model(
        self, view: Callable | None = None, status: int | None = None
    ) -> tuple[Any, bool]:
        """Resolve a status-specific, explicit, or inferred response contract."""
        if status is not None:
            status_models = dict(getattr(self, "_response_models", {}) or {})
            if view is not None and view is not self.endpoint:
                status_models.update(getattr(view, "_response_models", {}) or {})
            if status in status_models:
                return status_models[status], True
            if status >= 400:
                return None, False

        if self._stream_mode is not None:
            return None, False

        unset = object()
        explicit = unset
        if view is not None:
            explicit = getattr(view, "_response_model", unset)
        if explicit is unset:
            explicit = getattr(self.endpoint, "_response_model", unset)
        if explicit is not unset:
            return (None if explicit is False else explicit), True

        target = view or self.endpoint
        if inspect.isclass(target):
            return None, False
        return_hint = _view_return_hint(target)
        return inferred_response_model(return_hint), False

    def _response_model_failure(
        self,
        scope: Scope,
        response: Response,
        exc: Exception | None = None,
        *,
        model: Any = None,
        view: Callable | None = None,
    ) -> None:
        logger.error(
            "Response contract failed for %s %s in %s; expected %r",
            scope.get("method", "HTTP"),
            scope.get("route_pattern", scope.get("path", "?")),
            _callable_label(view or self.endpoint),
            model,
            exc_info=exc,
        )
        response.status_code = 500
        self._set_error_response(
            scope,
            response,
            500,
            INTERNAL_SERVER_ERROR,
            errors=_validation_errors(exc) if exc is not None else None,
            exc=exc,
        )

    def _validate_response_model(
        self, scope: Scope, response: Response, view: Callable | None = None
    ) -> None:
        status = response.status_code if response.status_code is not None else 200
        resp_model, explicit_model = self._response_model(view, status)
        if resp_model is None:
            return

        if not response_status_allows_body(status):
            return

        kind = "media" if explicit_model else response_body_kind(resp_model)
        if response._stream is not None or response._deferred_content is not None:
            exc = TypeError(
                "A streaming or file response cannot satisfy a typed response "
                "contract; use response_model=False"
            )
            if getattr(scope.get("api"), "debug", False):
                raise exc
            self._response_model_failure(
                scope, response, exc, model=resp_model, view=view
            )
            return

        if response.media is not None or (response.content is None and kind == "media"):
            value = response.media
            target = "media"
        elif response.content is not None and kind in ("text", "bytes"):
            value = response.content
            target = kind
        else:
            exc = TypeError(
                f"The response body uses a channel incompatible with {resp_model!r}"
            )
            if getattr(scope.get("api"), "debug", False):
                raise exc
            self._response_model_failure(
                scope, response, exc, model=resp_model, view=view
            )
            return

        try:
            adapter = response_type_adapter(resp_model)
            validated = adapter.validate_python(value)
            if target == "media":
                response.media = adapter.dump_python(validated, mode="json")
            else:
                response.content = adapter.dump_python(validated, mode="python")
        except Exception as exc:
            if getattr(scope.get("api"), "debug", False):
                raise
            self._response_model_failure(
                scope, response, exc, model=resp_model, view=view
            )

    async def _send_timeout_response(
        self, scope: Scope, receive: Receive, send: Send, response: Response
    ) -> None:
        """Send a 504 on ``response`` — a fresh object carrying only the
        metadata snapshotted from the abandoned response (hook-set headers,
        cookies). Content is built directly rather than via
        ``_set_error_response``: its ``reset_for_error()`` would wipe that
        carried-over metadata, and there is no stale body here to reset.
        """
        response.status_code = 504
        if scope.get("problem_details"):
            response.content = problem_bytes_for(
                scope, 504, "Request timed out", request=response.req
            )
            self._problem_content_type(scope, response)
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

        # Seed the route's declared default success status (route(status_code=…))
        # after the hooks ran — a hook that sets resp.status_code skips the
        # handler, and an explicit assignment in the view still wins.
        default_status = getattr(self.endpoint, "_default_status_code", None)
        if default_status is not None:
            response.status_code = default_status

        route_csrf = getattr(self, "_csrf", None)
        if scope.get("csrf_enabled", False) if route_csrf is None else route_csrf:
            _trace(scope, "csrf")
            from .csrf import enforce_csrf

            await enforce_csrf(request)

        _trace(scope, "auth")
        auth_injected = await self._route_auth_injections(request)
        views = self._views_for(request)
        ok, injections = await self._validate_inputs(
            scope, receive, send, request, response, path_params, views
        )
        if not ok:
            return

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
                    injections,
                    auth_injected,
                )
                timeout = scope.get("request_timeout")
                if timeout:
                    await asyncio.wait_for(run, timeout)
                else:
                    await run
            except asyncio.TimeoutError:
                # An abandoned sync view (stuck in the threadpool — it cannot
                # be cancelled) may keep mutating ``response`` after the
                # timeout fires; build the 504 on a fresh Response so its late
                # writes can't corrupt what we send. Headers and cookies that
                # were legitimately set before the view hung (before_request
                # hooks finish before the view starts) still belong on the
                # 504, so snapshot them onto the fresh object.
                timeout_response = self._exchange(scope, receive)[1]
                _copy_response_metadata(response, timeout_response)
                await self._send_timeout_response(scope, receive, send, timeout_response)
                return
            except _MarkerValidationError as exc:
                await self._send_validation_error(scope, receive, send, response, exc)
                return

            await self._run_after_hooks(scope, request, response)
            if response.status_code is None:
                response.status_code = status_codes.HTTP_200
            await self._prepare_typed_stream(scope, response, views[-1])
            self._validate_response_model(scope, response, views[-1])
            await response(scope, receive, send)
        finally:
            await self._close_abandoned_typed_streams(
                response, include_pending=True
            )
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
        if not route.startswith("/"):
            raise ValueError(f"Route path must start with '/', got {route!r}.")
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
        """The route's URL with ``params`` substituted (values URL-quoted;
        ``{param:path}`` segments keep their slashes)."""
        return self._url_template.format(
            **_quote_url_params(params, self.param_convertor_names)
        )

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
            # Query()/Header()/Cookie()/Path() markers resolve from the
            # WebSocket handshake (query string, headers, cookies) exactly as
            # they do for HTTP views; a validation failure raises
            # _MarkerValidationError, closing the socket with 1008 below.
            marker_values, drop_keys = await _resolve_markers(
                self.endpoint, ws, path_params
            )
            for key in drop_keys:
                kwargs.pop(key, None)
            kwargs.update(marker_values)
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
        except WebSocketDisconnect:
            # The client went away mid-handler; nothing to close or report.
            raise
        except Exception:
            # An unhandled handler exception: close with 1011 (internal
            # error) so clients can tell a server bug from a network drop,
            # then re-raise for the server / test client to surface.
            logger.exception("Unhandled exception in WebSocket handler")
            await self._close_if_connected(ws, code=1011)
            raise
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

    async def _run_before_hooks(self, hooks: Iterable[Callable], ws: WebSocket) -> bool:
        for hook in hooks:
            await self._dispatch_hook(hook, ws)
            # If a hook closed the connection, short-circuit the endpoint.
            if WebSocketState.DISCONNECTED in (ws.client_state, ws.application_state):
                return False
        return True

    async def _run_after_hooks(self, hooks: Iterable[Callable], ws: WebSocket) -> None:
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
                if _is_request_param(pname, ann, _WS_REQUEST_NAMES | _HTTP_REQUEST_NAMES):
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
        csrf: bool = False,
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
        self.csrf = csrf
        self._route_cache: dict[tuple[str, str], tuple[BaseRoute, dict]] = {}
        # Bumped whenever the route table changes; cheap invalidation key for
        # derived artifacts (e.g. the cached OpenAPI document).
        self._generation = 0
        self.formats: dict[str, Callable] = get_formats() if formats is None else formats
        self._lifespan_handler = lifespan
        # Route wrapper for a Responder-view default endpoint (built lazily).
        self._default_route: Route | None = None
        # Prepared (normalized prefix, ASGI app) mounts, rebuilt when
        # ``self.apps`` changes — see ``_sorted_mounts``.
        self._mounts: list[tuple[str, Any]] = []
        self._mounts_snapshot: tuple | None = None

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

        # Freeze this registration's per-route CSRF override onto the Route
        # itself. Reading it off the endpoint at dispatch time would leak the
        # value across re-registrations of a shared function, or inherit it
        # through a CBV's MRO. For classes, read the class's own __dict__ so a
        # subclass never picks up a base's exemption. ``None`` = inherit the
        # app-wide default.
        if inspect.isclass(endpoint):
            new_route._csrf = endpoint.__dict__.get("_csrf")
            route_response_models = endpoint.__dict__.get("_response_models", {})
        else:
            new_route._csrf = getattr(endpoint, "_csrf", None)
            route_response_models = getattr(endpoint, "_response_models", {})
        if isinstance(new_route, Route):
            new_route._response_models = dict(route_response_models or {})
            new_route._stream_mode = getattr(endpoint, "_stream_mode", None)
            new_route._stream_model = getattr(endpoint, "_stream_model", None)
            new_route._stream_heartbeat = getattr(endpoint, "_stream_heartbeat", None)

        self.routes.append(new_route)
        self._route_cache.clear()
        self._generation += 1

    def mount(self, route: str, app: Any) -> None:
        """Mounts ASGI / WSGI applications at a given route.

        The prefix is normalized (a trailing slash is stripped, so
        ``/admin/`` and ``/admin`` are equivalent; ``""`` mounts at the
        root), and a WSGI app is wrapped for ASGI dispatch once, here,
        rather than per request.
        """
        route = route.rstrip("/")
        if _is_wsgi_app(app):
            from a2wsgi import WSGIMiddleware

            app = WSGIMiddleware(app)
        self.apps.update({route: app})

    def add_event_handler(self, event_type: str, handler: Callable) -> None:
        if event_type not in ("startup", "shutdown"):
            raise ValueError(
                f"Only 'startup' and 'shutdown' events are supported, not {event_type!r}."
            )
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
                if _is_request_param(pname, ann, _WS_REQUEST_NAMES | _HTTP_REQUEST_NAMES):
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

    def url_for(self, endpoint: Callable | str, **params: Any) -> str:
        # An explicit route name wins (decouples reversal from function identity,
        # so lambdas and shared function names are addressable).
        if isinstance(endpoint, str):
            for route in self.routes:
                if getattr(route, "name", None) == endpoint:
                    return route.url(**params)
        for route in self.routes:
            # Callable-instance endpoints (e.g. class-based views registered as
            # objects) have no __name__; never let a missing name match anything.
            endpoint_name = getattr(route.endpoint, "__name__", None)
            if endpoint == route.endpoint or (
                endpoint_name is not None and endpoint == endpoint_name
            ):
                return route.url(**params)
        raise RouteNotFoundError(f"No route is registered for {endpoint!r}")

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
            # LRU: re-insert on hit so hot entries survive eviction (below).
            self._route_cache.pop(key, None)
            self._route_cache[key] = cached
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
                    # Evict the least-recently-used entry instead of clearing
                    # wholesale, so high-cardinality parameterized paths can't
                    # thrash the hot static entries.
                    self._route_cache.pop(next(iter(self._route_cache)), None)
                self._route_cache[key] = (route, _fresh_child_scope(child_scope))
                return route
        return None

    async def lifespan(self, scope: Scope, receive: Receive, send: Send) -> None:
        message = await receive()
        assert message["type"] == "lifespan.startup"

        if self._lifespan_handler is not None:
            # Modern lifespan context manager pattern. Expose the API as
            # scope["app"] so a standard ``async def lifespan(app)`` receives
            # the application instead of None. on_event handlers still fire
            # (LIFO around the context manager): startup events run after
            # ``__aenter__``, shutdown events before ``__aexit__`` — so e.g.
            # the API's background-task draining isn't silently skipped.
            scope["app"] = self.api
            try:
                ctx = self._lifespan_handler(scope["app"])
                await ctx.__aenter__()
            except BaseException:
                msg = traceback.format_exc()
                await send({"type": "lifespan.startup.failed", "message": msg})
                raise
            try:
                await self.trigger_event("startup")
            except BaseException:
                msg = traceback.format_exc()
                try:
                    await ctx.__aexit__(*sys.exc_info())
                except Exception:
                    logger.exception("Lifespan exit failed after startup error")
                await send({"type": "lifespan.startup.failed", "message": msg})
                raise

            await send({"type": "lifespan.startup.complete"})
            message = await receive()
            assert message["type"] == "lifespan.shutdown"

            try:
                try:
                    await self.trigger_event("shutdown")
                finally:
                    await ctx.__aexit__(None, None, None)
            except BaseException:
                # A raising __aexit__ (or shutdown event) must still reach
                # app-dependency teardown and report the failure.
                msg = traceback.format_exc()
                await self.app_dependencies.shutdown()
                await send({"type": "lifespan.shutdown.failed", "message": msg})
                raise
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
            try:
                await self.trigger_event("shutdown")
            except BaseException:
                # A raising shutdown handler must still reach app-dependency
                # teardown and report the failure (mirrors the lifespan= path).
                msg = traceback.format_exc()
                await self.app_dependencies.shutdown()
                await send({"type": "lifespan.shutdown.failed", "message": msg})
                raise
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
        scope["csrf_enabled"] = self.csrf

        if route is not None:
            await route(scope, receive, send)
            return

        # Call into a submounted app, if one exists. Longer (more specific)
        # prefixes win, and a prefix only matches on a path-segment boundary
        # so that, e.g., "/subscribe" is not mis-routed into a mount at "/sub"
        # (the empty-prefix root catch-all still matches everything).
        for path_prefix, app in self._sorted_mounts():
            if path == path_prefix or path.startswith(path_prefix + "/"):
                scope["path"] = path[len(path_prefix) :] or "/"
                scope["root_path"] = root_path + path_prefix
                await app(scope, receive, send)
                return

        # A near-miss on the trailing slash gets redirected to the real route,
        # preserving the method and query string (307).
        if scope["type"] == "http" and self.redirect_slashes and path != "/":
            alternate = path[:-1] if path.endswith("/") else path + "/"
            # Match by path only (not method): a POST to the slashed variant
            # redirects just like a GET — 307 preserves the method, and the
            # follow-up request earns the proper 405 + Allow if needed.
            if any(
                route.path_re.match(alternate)
                for route in self.routes
                if isinstance(route, Route)
            ):
                query_string = scope.get("query_string", b"")
                # Percent-encode the decoded path so the Location header is
                # latin-1 safe (same safe set as Starlette's RedirectResponse).
                location = urllib.parse.quote(alternate, safe="/:@&=+$,;~*!')(") + (
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
                        PROBLEM_JSON if self.problem_details else "application/json"
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

        await self._dispatch_default(scope, receive, send)

    def _sorted_mounts(self) -> list[tuple[str, Any]]:
        """Mounted apps as ``(normalized prefix, ASGI app)``, longest first.

        ``self.apps`` may also be populated directly (``API.mount`` does), so
        prefix normalization and one-time WSGI wrapping are (re)applied here
        whenever the registry changes — never per request, since a fresh
        ``WSGIMiddleware`` costs a ``ThreadPoolExecutor`` per instance.
        """
        snapshot = tuple((prefix, id(app)) for prefix, app in self.apps.items())
        if snapshot != self._mounts_snapshot:
            mounts: list[tuple[str, Any]] = []
            for prefix, app in self.apps.items():
                if _is_wsgi_app(app):
                    from typing import cast

                    from a2wsgi import WSGIMiddleware

                    app = WSGIMiddleware(cast(Any, app))
                mounts.append((prefix.rstrip("/"), app))
            mounts.sort(key=lambda kv: len(kv[0]), reverse=True)
            self._mounts = mounts
            self._mounts_snapshot = snapshot
        return self._mounts

    async def _dispatch_default(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Invoke the default endpoint for an unmatched request.

        The built-in default (and any user-supplied ASGI-style callable) is
        invoked directly. A Responder view — e.g. one registered via
        ``add_route(default=True)`` — is dispatched through normal
        :class:`Route` semantics (hooks, response handling) with empty
        ``path_params``.
        """
        endpoint = self.default_endpoint
        if _is_asgi_style(endpoint):
            await endpoint(scope, receive, send)
            return
        if scope["type"] == "websocket":
            # A Responder HTTP view can't answer a websocket handshake;
            # close it exactly like the built-in default.
            await WebSocketClose()(scope, receive, send)
            return
        route = self._default_route
        if route is None or route.endpoint is not endpoint:
            route = self._default_route = Route("/", endpoint)
        scope["path_params"] = {}
        await route(scope, receive, send)

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
