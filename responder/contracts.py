"""Shared response-contract inference for runtime dispatch and OpenAPI."""

from __future__ import annotations

import inspect
import types
from typing import (
    Annotated,
    Any,
    Literal,
    Never,
    NoReturn,
    Union,
    get_args,
    get_origin,
)

_ADAPTER_CACHE: dict[Any, Any] = {}
_INFERRED_MODEL_CACHE: dict[Any, Any] = {}
_CACHE_MISS = object()


def response_type_adapter(tp: Any) -> Any:
    """Return a cached Pydantic ``TypeAdapter`` for a response contract."""
    try:
        return _ADAPTER_CACHE[tp]
    except (KeyError, TypeError):
        pass

    from pydantic import TypeAdapter

    adapter = TypeAdapter(tp)
    try:
        _ADAPTER_CACHE[tp] = adapter
    except TypeError:
        pass
    return adapter


def _unwrap_annotated(tp: Any) -> Any:
    while get_origin(tp) is Annotated:
        tp = get_args(tp)[0]
    return tp


def _tuple_body_annotation(annotation: Any) -> Any | None:
    """Extract the body type from a Flask-style tuple return annotation.

    Tuples are Responder's ``(body, status[, headers])`` control envelope, not
    a JSON-array return value. Only that documented shape is inferred.
    """
    candidate = _unwrap_annotated(annotation)
    if get_origin(candidate) is not tuple:
        return None
    args = get_args(candidate)
    if len(args) not in (2, 3):
        return None
    status = _unwrap_annotated(args[1])
    status_origin = get_origin(status)
    if status is int:
        return args[0]
    if status_origin is Literal:
        values = get_args(status)
        if values and all(isinstance(value, int) for value in values):
            return args[0]
    return None


def response_annotation_is_ignored(annotation: Any) -> bool:
    """Whether ``annotation`` intentionally declares no body contract."""
    if annotation in (
        None,
        type(None),
        Any,
        NoReturn,
        Never,
        inspect.Signature.empty,
        inspect.Parameter.empty,
    ):
        return True

    candidate = _unwrap_annotated(annotation)
    if inspect.isclass(candidate):
        try:
            from starlette.responses import Response as StarletteResponse

            from .models import Response

            return issubclass(candidate, (Response, StarletteResponse))
        except TypeError:
            return False
    return False


def inferred_response_model(annotation: Any) -> Any | None:
    """Return the enforceable response model represented by ``annotation``.

    Pydantic's ``TypeAdapter`` is the support boundary. ``Any``/``None`` and
    framework response classes carry no useful serializable contract. A
    Flask-style tuple annotation contributes its body type.
    """
    if response_annotation_is_ignored(annotation):
        return None

    cache_key = annotation
    try:
        cached = _INFERRED_MODEL_CACHE.get(cache_key, _CACHE_MISS)
    except TypeError:
        cached = _CACHE_MISS
    if cached is not _CACHE_MISS:
        return cached

    body_annotation = _tuple_body_annotation(annotation)
    if body_annotation is not None:
        annotation = body_annotation
    elif get_origin(_unwrap_annotated(annotation)) is tuple:
        return None

    result = annotation
    try:
        response_type_adapter(annotation).json_schema()
    except Exception:
        result = None
    try:
        _INFERRED_MODEL_CACHE[cache_key] = result
    except TypeError:
        pass
    return result


def response_body_kind(model: Any) -> str:
    """Return ``media``, ``text``, or ``bytes`` for a response model."""
    model = _unwrap_annotated(model)
    origin = get_origin(model)
    if origin in (Union, types.UnionType):
        branches = get_args(model)
        if any(branch is type(None) for branch in branches):
            return "media"
        kinds = {response_body_kind(branch) for branch in branches}
        return kinds.pop() if len(kinds) == 1 else "media"
    if origin is Literal:
        values = get_args(model)
        if values and all(isinstance(value, str) for value in values):
            return "text"
        if values and all(isinstance(value, bytes) for value in values):
            return "bytes"
    if model is str:
        return "text"
    if model is bytes:
        return "bytes"
    return "media"


def response_media_type(model: Any) -> str:
    """Return the default HTTP media type for ``model``."""
    return {
        "text": "text/plain",
        "bytes": "application/octet-stream",
        "media": "application/json",
    }[response_body_kind(model)]


def response_status_allows_body(status: int) -> bool:
    """Whether RFC 9110 permits content on ``status``."""
    return not (100 <= status < 200 or status in (204, 205, 304))
