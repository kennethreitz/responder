"""Readable descriptions of the routes registered on a Responder API.

Used by ``responder routes``. The same facts are available as dictionaries
for ``--json``.
"""

from __future__ import annotations

import json
import types
from typing import Any, Union, get_args, get_origin

_METHOD_ORDER = (
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
    "TRACE",
    "CONNECT",
)
_SKIP_PARAMS = frozenset(
    {"self", "cls", "req", "request", "resp", "response", "ws", "websocket"}
)
_BORING = frozenset({str, bytes, int, float, bool, dict, list, tuple, set, type(None)})


def describe_routes(api: Any) -> list[dict[str, Any]]:
    """One row per registered route, in registration order.

    WebSocket routes are included. Mounted sub-applications are not: they are
    not Responder routes.
    """
    rows = []
    for route in getattr(api.router, "routes", ()):
        methods = _declared_methods(route)
        request_model = _request_label(api, route, methods)
        response_model = _response_label(route, methods)
        rows.append(
            {
                "methods": methods,
                "path": getattr(route, "path_template", route.route),
                "endpoint": _endpoint_name(route.endpoint),
                "request_model": request_model,
                "response_model": response_model,
                "dependencies": _dependency_names(api, route, methods),
            }
        )
    return rows


def format_routes(rows: list[dict[str, Any]]) -> str:
    """Render :func:`describe_routes` as a plain-text table."""
    headers = ("Methods", "Path", "Endpoint", "Request", "Response", "Dependencies")
    table = [_row_cells(row) for row in rows]
    widths = [len(header) for header in headers]
    for cells in table:
        for index, cell in enumerate(cells):
            widths[index] = max(widths[index], len(cell))

    def line(cells: tuple[str, ...]) -> str:
        return "  ".join(
            cell.ljust(widths[index]) for index, cell in enumerate(cells)
        ).rstrip()

    rendered = [line(headers), line(tuple("-" * width for width in widths))]
    rendered.extend(line(cells) for cells in table)
    if not table:
        rendered.append("(no routes)")
    return "\n".join(rendered) + "\n"


def routes_json(rows: list[dict[str, Any]]) -> str:
    """Render :func:`describe_routes` as indented JSON."""
    return json.dumps(rows, indent=2) + "\n"


def render_routes(api: Any, *, as_json: bool = False) -> str:
    """Text table, or JSON when ``as_json`` is set."""
    rows = describe_routes(api)
    if as_json:
        return routes_json(rows)
    return format_routes(rows)


def _row_cells(row: dict[str, Any]) -> tuple[str, str, str, str, str, str]:
    methods = row["methods"]
    method_text = "*" if methods == ["*"] else ",".join(methods)
    dependencies = ", ".join(row["dependencies"])
    return (
        method_text,
        row["path"],
        row["endpoint"],
        row["request_model"] or "—",
        row["response_model"] or "—",
        dependencies or "—",
    )


def _endpoint_name(endpoint: Any) -> str:
    return getattr(endpoint, "__name__", None) or type(endpoint).__name__


def _declared_methods(route: Any) -> list[str]:
    from responder.routes import WebSocketRoute

    if isinstance(route, WebSocketRoute):
        return ["WEBSOCKET"]
    methods = getattr(route, "methods", None)
    if methods:
        return _sort_methods(methods)
    endpoint = route.endpoint
    if isinstance(endpoint, type):
        found = []
        for name in dir(endpoint):
            if not name.startswith("on_"):
                continue
            verb = name[3:].upper()
            if verb in _METHOD_ORDER and callable(getattr(endpoint, name, None)):
                found.append(verb)
        if found:
            return _sort_methods(found)
    return ["*"]


def _sort_methods(methods: Any) -> list[str]:
    rank = {method: index for index, method in enumerate(_METHOD_ORDER)}
    return sorted(
        (str(method).upper() for method in methods),
        key=lambda method: (rank.get(method, len(_METHOD_ORDER)), method),
    )


def _operation_views(route: Any, methods: list[str]) -> list[tuple[str | None, Any]]:
    endpoint = route.endpoint
    if not isinstance(endpoint, type):
        return [(None, endpoint)]
    views: list[tuple[str | None, Any]] = []
    for method in methods:
        if method in {"*", "WEBSOCKET"}:
            continue
        view = getattr(endpoint, f"on_{method.lower()}", None)
        if view is not None:
            views.append((method, view))
    if not views:
        on_request = getattr(endpoint, "on_request", None)
        if on_request is not None:
            views.append((None, on_request))
    return views or [(None, endpoint)]


def _request_label(api: Any, route: Any, methods: list[str]) -> str | None:
    from .openapi import _body_parameter, _is_form_model, _marker_specs

    dep_names = set(getattr(getattr(api, "router", None), "dependencies", {}) or {})
    labels: list[tuple[str | None, str]] = []
    for method, view in _operation_views(route, methods):
        if isinstance(view, type):
            continue
        label = None
        body = _body_parameter(view, route, dep_names)
        if body is not None:
            label = _model_label(body[1])
        if label is None:
            params_model = getattr(view, "_params_model", None) or getattr(
                route.endpoint, "_params_model", None
            )
            label = _model_label(params_model)
        if label is None:
            for spec in _marker_specs(view):
                if spec.location == "form" and _is_form_model(spec.annotation):
                    label = _model_label(spec.annotation)
                    break
        if label:
            labels.append((method, label))
    return _join_labels(labels)


def _response_label(route: Any, methods: list[str]) -> str | None:
    from .openapi import _response_model

    stream_model = getattr(route, "_stream_model", None)
    stream_mode = getattr(route, "_stream_mode", None)
    labels: list[tuple[str | None, str]] = []
    if stream_model is not None:
        label = _model_label(stream_model) or _type_label(stream_model)
        if stream_mode and label:
            label = f"{label} ({stream_mode})"
        if label:
            return label
    for method, view in _operation_views(route, methods):
        model = None if isinstance(view, type) else _response_model(view)
        if model is None and view is not route.endpoint:
            model = _response_model(route.endpoint)
        response = _model_label(model)
        if response:
            labels.append((method, response))
    return _join_labels(labels)


def _join_labels(labels: list[tuple[str | None, str]]) -> str | None:
    if not labels:
        return None
    unique = []
    for _method, label in labels:
        if label not in unique:
            unique.append(label)
    if len(unique) == 1:
        return unique[0]
    parts = []
    for method, label in labels:
        parts.append(label if method is None else f"{method}:{label}")
    return ", ".join(parts)


def _dependency_names(api: Any, route: Any, methods: list[str]) -> list[str]:
    from responder.routes import _depends_params, _view_param_names

    registry = set(getattr(getattr(api, "router", None), "dependencies", {}) or {})
    names: list[str] = []
    for dep in getattr(route.endpoint, "_route_dependencies", ()) or ():
        names.append(_provider_name(dep))
    for _method, view in _operation_views(route, methods):
        if isinstance(view, type) or not callable(view):
            continue
        try:
            depends = _depends_params(view)
        except Exception:
            depends = {}
        names.extend(_provider_name(dep) for dep in depends.values())
        try:
            params = _view_param_names(view, skip=0)
        except Exception:
            params = ()
        for name in params:
            if name in _SKIP_PARAMS:
                continue
            if name in registry:
                names.append(name)
    unique: list[str] = []
    for name in names:
        if name not in unique:
            unique.append(name)
    return unique


def _provider_name(dep: Any) -> str:
    provider = getattr(dep, "provider", None)
    if provider is None:
        return "dependency"
    return getattr(provider, "__name__", None) or provider.__class__.__name__


def _model_label(model: Any) -> str | None:
    if model is None or model is False or _is_boring(model):
        return None
    return _type_label(model)


def _is_boring(model: Any) -> bool:
    if model is None or model is type(None):
        return True
    if isinstance(model, type) and model in _BORING:
        return True
    origin = get_origin(model)
    if origin in (list, dict, tuple, set, frozenset, Union, types.UnionType):
        return all(_is_boring(arg) for arg in get_args(model))
    return False


def _type_label(model: Any) -> str:
    if model is type(None):
        return "None"
    origin = get_origin(model)
    if isinstance(model, type):
        return model.__name__
    if origin is None:
        return getattr(model, "__name__", None) or str(model)
    args = get_args(model)
    if origin in (Union, types.UnionType):
        return " | ".join(_type_label(arg) for arg in args)
    origin_name = getattr(origin, "__name__", None) or str(origin).rsplit(".", 1)[-1]
    inner = ", ".join(_type_label(arg) for arg in args)
    return f"{origin_name}[{inner}]"
