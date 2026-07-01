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
from typing import Annotated, Any, NamedTuple, get_args, get_origin

try:
    from pydantic import Field as _Field
    from pydantic import TypeAdapter as _TypeAdapter
except ImportError:  # pragma: no cover - pydantic is a core dep
    _TypeAdapter = None  # type: ignore[assignment,misc]
    _Field = None  # type: ignore[assignment]

__all__ = ["Query", "Header", "Cookie", "Path", "Form", "File", "Depends"]

# Pydantic ``Field`` constraints accepted on a marker. Anything else passed as a
# keyword is treated as a typo and rejected, rather than silently swallowed (a
# stray kwarg used to land in ``constraints`` and leave the parameter required).
_FIELD_CONSTRAINTS = frozenset(
    {
        "gt",
        "ge",
        "lt",
        "le",
        "multiple_of",
        "allow_inf_nan",
        "min_length",
        "max_length",
        "pattern",
        "max_digits",
        "decimal_places",
        "strict",
        "examples",
    }
)


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
        unknown = set(constraints) - _FIELD_CONSTRAINTS
        if unknown:
            raise TypeError(
                f"{location.title()}() got unexpected keyword argument(s): "
                f"{', '.join(sorted(unknown))}. Valid constraints are: "
                f"{', '.join(sorted(_FIELD_CONSTRAINTS))}."
            )
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


def Form(default=..., **kwargs):  # noqa: N802
    """Mark a parameter as a form field (urlencoded or multipart body)."""
    return _Marker("form", default, **kwargs)


def File(default=..., **kwargs):  # noqa: N802
    """Mark a parameter as an uploaded file; injects an ``UploadFile`` (or a
    ``list`` of them for a sequence annotation)."""
    return _Marker("file", default, **kwargs)


class _Depends:
    """A dependency marker used as a handler-parameter default."""

    __slots__ = ("provider",)

    def __init__(self, provider):
        if not callable(provider):
            raise TypeError("Depends() requires a callable provider")
        self.provider = provider

    def __repr__(self):
        name = getattr(self.provider, "__name__", repr(self.provider))
        return f"Depends({name})"


def Depends(provider):  # noqa: N802 - marker factory, FastAPI-style
    """Inject the value returned by ``provider`` into a handler parameter.

    Unlike named ``api.dependency(...)`` providers, ``Depends`` is local to one
    handler and does not require registration on the app.
    """
    return _Depends(provider)


def _is_sequence(annotation: Any) -> bool:
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


def _annotated_marker(annotation):
    """The ``_Marker`` carried in an ``Annotated`` annotation, if any.

    Also unwraps ``Optional[Annotated[...]]`` forms so older annotations or
    explicit ``Optional`` aliases do not hide marker metadata behind a ``Union``.
    """
    for candidate in (annotation, *get_args(annotation)):
        for meta in getattr(candidate, "__metadata__", ()):
            if isinstance(meta, _Marker):
                return meta
    return None


_PARAM_CACHE: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def marker_params(handler: Any, hints: dict) -> tuple[ParamSpec, ...]:
    """The marker-driven ``ParamSpec``s for ``handler`` (cached per function)."""
    import inspect

    key = getattr(handler, "__func__", handler)
    try:
        return _PARAM_CACHE[key]
    except (KeyError, TypeError):
        pass

    # PEP 593 markers (``q: Annotated[int, Query()]``) carry the marker in the
    # annotation's metadata rather than the parameter default; resolve those once.
    # (The ``hints`` passed in already strip Annotated down to the inner type.)
    try:
        import typing

        extras = typing.get_type_hints(handler, include_extras=True)
    except Exception:
        extras = {}

    specs: list[ParamSpec] = []
    params: Any
    try:
        params = inspect.signature(handler).parameters
    except (TypeError, ValueError):
        params = {}
    for pname, param in params.items():
        marker = param.default if isinstance(param.default, _Marker) else None
        if marker is None:
            marker = _annotated_marker(extras.get(pname))
        if marker is None:
            continue
        annotation = hints.get(pname, str)
        if marker.alias:
            lookup = marker.alias
        elif marker.location == "header" and marker.convert_underscores:
            lookup = pname.replace("_", "-")
        else:
            lookup = pname
        adapter = None
        # File uploads inject an UploadFile, not a coerced scalar — no adapter.
        if (
            marker.location != "file"
            and _TypeAdapter is not None
            and annotation is not inspect.Parameter.empty
        ):
            target = annotation
            if marker.constraints and _Field is not None:
                target = Annotated[annotation, _Field(**marker.constraints)]
            try:
                adapter = _TypeAdapter(target)
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


def raw_value(spec: ParamSpec, request: Any, path_params: dict) -> Any:
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
