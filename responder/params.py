"""Type-hint-driven parameter markers for handler signatures.

Use these as parameter defaults to inject validated query parameters, headers,
cookies, or path parameters as handler arguments::

    from responder import Query, Header

    @api.route("/search")
    async def search(req, resp, *, q: str = Query(...), token: str = Header(None)):
        ...

``Query(...)`` (Ellipsis) marks a required parameter; ``Query(default)`` supplies
a default. Values are validated/coerced against the parameter's type annotation
with Pydantic; a failure yields a ``422``.
"""

from __future__ import annotations

import weakref
from typing import Any, NamedTuple, get_origin

try:
    from pydantic import TypeAdapter as _TypeAdapter
except ImportError:  # pragma: no cover - pydantic is a core dep
    _TypeAdapter = None  # type: ignore[assignment,misc]

__all__ = ["Query", "Header", "Cookie", "Path"]


class _Marker:
    """A parameter source marker, used as a handler-parameter default."""

    __slots__ = (
        "location",
        "default",
        "alias",
        "convert_underscores",
        "description",
        "deprecated",
        "constraints",
    )

    def __init__(
        self,
        location,
        default=...,
        *,
        alias=None,
        convert_underscores=True,
        description=None,
        deprecated=False,
        **constraints,
    ):
        self.location = location
        self.default = default
        self.alias = alias
        self.convert_underscores = convert_underscores
        self.description = description
        self.deprecated = deprecated
        self.constraints = constraints

    @property
    def required(self):
        return self.default is ...

    def __repr__(self):
        return f"{self.location.title()}(default={self.default!r}, alias={self.alias!r})"


def Query(default=..., **kwargs):  # noqa: N802 - marker factory, FastAPI-style
    """Mark a parameter as coming from the query string."""
    return _Marker("query", default, **kwargs)


def Header(default=..., **kwargs):  # noqa: N802
    """Mark a parameter as coming from a request header (``_`` → ``-`` by default)."""
    return _Marker("header", default, **kwargs)


def Cookie(default=..., **kwargs):  # noqa: N802
    """Mark a parameter as coming from a request cookie."""
    return _Marker("cookie", default, **kwargs)


def Path(default=..., **kwargs):  # noqa: N802
    """Mark a parameter as coming from a path parameter."""
    return _Marker("path", default, **kwargs)


def _is_sequence(annotation) -> bool:
    return annotation in (list, tuple, set, frozenset) or get_origin(annotation) in (
        list,
        tuple,
        set,
        frozenset,
    )


class ParamSpec(NamedTuple):
    """A resolved marker parameter: where to read it and how to validate it."""

    name: str
    location: str
    annotation: Any
    marker: _Marker
    lookup: str  # the request key (alias, or derived from the name)
    is_sequence: bool
    adapter: Any  # a pydantic TypeAdapter, or None when unannotated

    @property
    def required(self) -> bool:
        return self.marker.required


_PARAM_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def marker_params(handler, hints) -> tuple[ParamSpec, ...]:
    """The marker-driven ``ParamSpec``s for ``handler`` (cached per function)."""
    import inspect

    key = getattr(handler, "__func__", handler)
    try:
        return _PARAM_CACHE[key]
    except (KeyError, TypeError):
        pass

    specs: list[ParamSpec] = []
    params: Any
    try:
        params = inspect.signature(handler).parameters
    except (TypeError, ValueError):
        params = {}
    for pname, param in params.items():
        marker = param.default
        if not isinstance(marker, _Marker):
            continue
        annotation = hints.get(pname, str)
        if marker.alias:
            lookup = marker.alias
        elif marker.location == "header" and marker.convert_underscores:
            lookup = pname.replace("_", "-")
        else:
            lookup = pname
        adapter = None
        if _TypeAdapter is not None and annotation is not inspect.Parameter.empty:
            try:
                adapter = _TypeAdapter(annotation)
            except Exception:
                adapter = None
        specs.append(
            ParamSpec(
                name=pname,
                location=marker.location,
                annotation=annotation,
                marker=marker,
                lookup=lookup,
                is_sequence=_is_sequence(annotation),
                adapter=adapter,
            )
        )

    result = tuple(specs)
    try:
        _PARAM_CACHE[key] = result
    except TypeError:
        pass
    return result


def raw_value(spec: ParamSpec, request, path_params):
    """Pull the raw (pre-validation) value for ``spec`` from the request.

    Returns ``...`` (Ellipsis) when the value is absent.
    """
    loc = spec.location
    if loc == "query":
        params = request.params
        if spec.is_sequence:
            values = params.get_list(spec.lookup)
            return values if values else ...
        return params.get(spec.lookup, ...)
    if loc == "header":
        return request.headers.get(spec.lookup, ...)
    if loc == "cookie":
        return request.cookies.get(spec.lookup, ...)
    if loc == "path":
        return path_params.get(spec.lookup, ...)
    return ...  # pragma: no cover
