"""Opt-in Model Context Protocol endpoint for a Responder API.

The official ``mcp`` Python SDK can serve Streamable HTTP, but its ASGI app
does not mount cleanly: the session manager has to be entered from the host
application's lifespan (a mounted sub-app's lifespan never runs), and the
transport rejects unfamiliar ``Host`` headers until its own allow-list is
configured. Responder therefore speaks the small Streamable HTTP surface
itself — ``initialize``, ``notifications/*``, ``ping``, ``tools/list``, and
``tools/call`` — and dispatches each tool call through the normal ASGI stack.
Nothing here is imported unless :meth:`responder.API.mcp` is used, and there
is no extra dependency.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

from .openapi import (
    _body_parameter,
    _doc_methods,
    _form_model_schema,
    _is_form_model,
    _json_schema_from_adapter,
    _marker_parameters,
    _marker_specs,
    _operation_endpoint,
    _operation_meta,
    _path_parameters,
    _query_parameters,
    _response_model,
)

logger = logging.getLogger("responder.mcp")

# Protocol revisions this endpoint can negotiate. A client asking for anything
# else is answered with the newest revision and may disconnect.
PROTOCOL_VERSIONS = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
)
LATEST_PROTOCOL = PROTOCOL_VERSIONS[-1]

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

_PATH_PARAM = re.compile(r"{([a-zA-Z_][a-zA-Z0-9_]*)}")
_COMPONENT_REF = "#/components/schemas/"
_LOCAL_REF = "#/$defs/"

# Headers that describe the JSON-RPC message itself. The dispatched request
# builds its own framing, and MCP session headers are not part of the route.
_DROP_HEADERS = frozenset(
    {
        b"content-type",
        b"content-length",
        b"content-encoding",
        b"transfer-encoding",
        b"accept",
        b"accept-encoding",
        b"connection",
        b"keep-alive",
        b"upgrade",
        b"te",
        b"trailer",
        b"mcp-protocol-version",
        b"mcp-session-id",
    }
)

_SKIP_ENDPOINT_NAMES = frozenset(
    {
        "_static_response",
        "_metrics_view",
        "_health_view",
        "schema_response",
        "docs_response",
        "mcp_endpoint",
        "GraphQLView",
    }
)

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "", ";"})


@dataclass
class Tool:
    """One route method published as an MCP tool."""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    title: str | None
    method: str
    path_template: str
    locations: dict[str, str]
    annotations: dict[str, bool]

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": dict(self.annotations),
        }
        if self.title:
            payload["title"] = self.title
        if self.output_schema is not None:
            payload["outputSchema"] = self.output_schema
        return payload


def mount_mcp(
    api: Any,
    path: str,
    *,
    include: str,
    name: str | None,
    instructions: str | None,
) -> None:
    """Register the MCP endpoint on ``api`` and remember how routes are chosen."""
    if include not in ("all", "explicit"):
        raise ValueError("include= must be 'all' or 'explicit'")
    if not isinstance(path, str) or not path.startswith("/") or "{" in path:
        raise ValueError("MCP path must be a static URL path starting with '/'")
    if len(path) > 1:
        path = path.rstrip("/")
    if getattr(api, "_mcp_path", None):
        raise RuntimeError(
            f"MCP is already mounted at {api._mcp_path!r}. "
            "An API exposes one MCP endpoint."
        )

    endpoint = MCPEndpoint(
        api,
        path=path,
        include=include,
        name=_server_name(api, name),
        version=_server_version(api),
        instructions=instructions
        if instructions is not None
        else (
            "Each tool is an HTTP route on this API. A call runs through the "
            "normal request pipeline, including validation, dependencies, and "
            "auth."
        ),
    )
    try:
        api.add_route(path, endpoint)
    except ValueError as exc:
        raise ValueError(
            f"Cannot mount MCP at {path!r}: that path is already registered."
        ) from exc
    api._mcp_path = path
    api._mcp_endpoint = endpoint


class MCPEndpoint:
    """Streamable HTTP handler. One POST in, one JSON-RPC message out."""

    def __init__(
        self,
        api: Any,
        *,
        path: str,
        include: str,
        name: str,
        version: str,
        instructions: str,
    ) -> None:
        self.api = api
        self.path = path
        self.include = include
        self.name = name
        self.version = version
        self.instructions = instructions
        self.__name__ = "mcp_endpoint"
        self.__doc__ = "Model Context Protocol endpoint."
        # The endpoint is a machine protocol: CSRF tokens are not part of it,
        # and it is not an OpenAPI operation or a tool that calls itself.
        self._csrf = False
        self._include_in_schema = False
        self._mcp = False

    async def __call__(self, req: Any, resp: Any) -> None:
        method = req.method.upper()
        if method in {"GET", "DELETE"}:
            _method_not_allowed(resp, method)
            return
        if method != "POST":
            _method_not_allowed(resp, method)
            return

        origin = req.headers.get("origin")
        if origin and not _origin_allowed(origin, req.headers.get("host")):
            resp.status_code = 403
            resp.media = _error(None, INVALID_REQUEST, "Invalid Origin")
            return

        accept = _accept_mode(req.headers.get("accept"))
        if accept == "none":
            resp.status_code = 406
            resp.headers["Content-Type"] = "text/plain; charset=utf-8"
            resp.text = "Not Acceptable"
            return

        try:
            raw = await req.content
        except Exception:
            logger.exception("Failed to read MCP request body")
            _send(resp, _error(None, PARSE_ERROR, "Parse error"), accept)
            return

        try:
            message = json.loads(raw) if raw else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            _send(resp, _error(None, PARSE_ERROR, "Parse error"), accept)
            return

        if isinstance(message, list):
            _send(
                resp,
                _error(None, INVALID_REQUEST, "JSON-RPC batching is not supported"),
                accept,
            )
            return
        if not isinstance(message, dict):
            _send(resp, _error(None, INVALID_REQUEST, "Invalid Request"), accept)
            return

        rpc_method = message.get("method")
        protocol = req.headers.get("mcp-protocol-version")
        if (
            protocol
            and protocol not in PROTOCOL_VERSIONS
            and rpc_method != "initialize"
        ):
            resp.status_code = 400
            resp.media = _error(
                message.get("id"),
                INVALID_REQUEST,
                f"Unsupported MCP-Protocol-Version {protocol!r}",
            )
            return

        if isinstance(rpc_method, str) and rpc_method.startswith("notifications/"):
            resp.status_code = 202
            resp.content = b""
            return

        payload = await self._dispatch_message(req, message)
        _send(resp, payload, accept)

    async def _dispatch_message(self, req: Any, message: dict[str, Any]) -> dict:
        rpc_id = message.get("id") if "id" in message else None
        if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return _error(rpc_id, INVALID_REQUEST, "Invalid Request")

        rpc_method = message["method"]
        params = message.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(rpc_id, INVALID_PARAMS, "params must be an object")

        if rpc_method == "initialize":
            return _result(rpc_id, self._initialize(params))
        if rpc_method == "ping":
            return _result(rpc_id, {})
        if rpc_method == "tools/list":
            tools = collect_tools(self.api, include=self.include, mcp_path=self.path)
            return _result(rpc_id, {"tools": [tool.as_dict() for tool in tools]})
        if rpc_method == "tools/call":
            return await self._call_tool(req, rpc_id, params)
        return _error(rpc_id, METHOD_NOT_FOUND, f"Method not found: {rpc_method}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else LATEST_PROTOCOL
        result: dict[str, Any] = {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self.name, "version": self.version},
        }
        if self.instructions:
            result["instructions"] = self.instructions
        return result

    async def _call_tool(self, req: Any, rpc_id: Any, params: dict[str, Any]) -> dict:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return _error(rpc_id, INVALID_PARAMS, "Tool name is required")
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _error(rpc_id, INVALID_PARAMS, "Tool arguments must be an object")

        tools = collect_tools(self.api, include=self.include, mcp_path=self.path)
        tool = next((item for item in tools if item.name == name), None)
        if tool is None:
            return _error(rpc_id, INVALID_PARAMS, f"Unknown tool: {name}")

        try:
            status, body, headers = await _invoke_tool(self.api, req, tool, arguments)
        except KeyError as exc:
            missing = str(exc).strip("'")
            return _error(
                rpc_id, INVALID_PARAMS, f"Missing required argument {missing!r}"
            )
        except ValueError as exc:
            return _error(rpc_id, INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("MCP tool dispatch failed for %s", name)
            return _error(rpc_id, INTERNAL_ERROR, "Internal error")
        return _result(rpc_id, _tool_result(status, body, headers))


def collect_tools(api: Any, *, include: str, mcp_path: str) -> list[Tool]:
    """Tools currently published by ``api``, in route registration order."""
    from responder.routes import WebSocketRoute

    dep_names = set(getattr(api.router, "dependencies", {}) or {})
    skip_paths = _framework_paths(api, mcp_path)
    mcp_endpoint = getattr(api, "_mcp_endpoint", None)
    used: set[str] = set()
    tools: list[Tool] = []

    for route in api.router.routes:
        if isinstance(route, WebSocketRoute):
            continue
        if getattr(route, "_stream_mode", None):
            continue
        endpoint = route.endpoint
        if endpoint is mcp_endpoint:
            continue
        ep_name = getattr(endpoint, "__name__", type(endpoint).__name__)
        if ep_name in _SKIP_ENDPOINT_NAMES:
            continue
        path = getattr(route, "path_template", route.route)
        if path in skip_paths or path.rstrip("/") in skip_paths:
            continue
        flag = getattr(route, "_mcp", None)
        if flag is False:
            continue
        if include == "explicit" and flag is not True:
            continue
        if _requires_upload(endpoint):
            logger.debug("Skipping %s as an MCP tool: it requires a file upload", path)
            continue

        for method in _tool_methods(route, endpoint, dep_names):
            op_endpoint = _operation_endpoint(endpoint, method.lower())
            if _requires_upload(op_endpoint):
                logger.debug(
                    "Skipping %s %s as an MCP tool: it requires a file upload",
                    method,
                    path,
                )
                continue
            tool = _build_tool(
                route,
                endpoint,
                op_endpoint,
                method,
                path,
                dep_names,
                used,
            )
            if tool is not None:
                tools.append(tool)
    return tools


def _build_tool(
    route: Any,
    endpoint: Any,
    op_endpoint: Any,
    method: str,
    path: str,
    dep_names: set[str],
    used: set[str],
) -> Tool | None:
    meta = _operation_meta(endpoint, op_endpoint) or {}
    description, title = _description(endpoint, op_endpoint, meta, method, path)
    input_schema, locations = _input_schema(route, op_endpoint, dep_names)
    base = meta.get("operationId")
    if not base:
        if isinstance(endpoint, type):
            base = f"{getattr(endpoint, '__name__', 'view')}_{method.lower()}"
        else:
            base = getattr(endpoint, "__name__", None) or "tool"
    return Tool(
        name=_allocate_name(used, str(base), method),
        description=description,
        input_schema=input_schema,
        output_schema=_output_schema(endpoint, op_endpoint),
        title=title,
        method=method,
        path_template=path,
        locations=locations,
        annotations=_annotations(method),
    )


def _input_schema(
    route: Any, op_endpoint: Any, dep_names: set[str]
) -> tuple[dict[str, Any], dict[str, str]]:
    defs: dict[str, Any] = {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    locations: dict[str, str] = {}

    parameters = _path_parameters(route, op_endpoint, defs)
    if op_endpoint is not route.endpoint:
        parameters += _query_parameters(route.endpoint, defs)
    parameters += _query_parameters(op_endpoint, defs)
    parameters += _marker_parameters(op_endpoint, defs)
    for parameter in parameters:
        name = parameter.get("name")
        where = parameter.get("in")
        if not isinstance(name, str) or not isinstance(where, str) or name in properties:
            continue
        properties[name] = _parameter_schema(parameter)
        locations[name] = where
        if parameter.get("required") and name not in required:
            required.append(name)

    body = _body_parameter(op_endpoint, route, dep_names)
    if body is not None:
        _add_model_fields(
            properties,
            required,
            locations,
            defs,
            model=body[1],
            param_name=body[0],
            location="body",
        )
    else:
        _add_form_fields(op_endpoint, properties, required, locations, defs)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    if defs:
        schema["$defs"] = defs
    return _retarget_refs(schema), locations


def _add_form_fields(
    endpoint: Any,
    properties: dict[str, Any],
    required: list[str],
    locations: dict[str, str],
    defs: dict[str, Any],
) -> None:
    for spec in _marker_specs(endpoint):
        if spec.location == "file":
            continue
        if spec.location != "form":
            continue
        if _is_form_model(spec.annotation):
            schema = _form_model_schema(spec.annotation, False, defs)
            _add_schema_fields(
                properties,
                required,
                locations,
                schema,
                param_name=spec.name,
                location="form",
            )
            continue
        field_schema = _json_schema_from_adapter(spec.adapter, defs)
        if isinstance(field_schema, dict):
            field_schema.pop("title", None)
        if spec.lookup in properties:
            continue
        properties[spec.lookup] = field_schema
        locations[spec.lookup] = "form"
        if spec.required and spec.lookup not in required:
            required.append(spec.lookup)


def _add_model_fields(
    properties: dict[str, Any],
    required: list[str],
    locations: dict[str, str],
    defs: dict[str, Any],
    *,
    model: Any,
    param_name: str,
    location: str,
) -> None:
    try:
        schema = model.model_json_schema()
    except Exception:
        logger.debug("Could not build a JSON schema for %r", model, exc_info=True)
        return
    nested = schema.pop("$defs", None) or {}
    _merge_defs(defs, nested, schema)
    _add_schema_fields(
        properties,
        required,
        locations,
        schema,
        param_name=param_name,
        location=location,
    )


def _add_schema_fields(
    properties: dict[str, Any],
    required: list[str],
    locations: dict[str, str],
    schema: dict[str, Any],
    *,
    param_name: str,
    location: str,
) -> None:
    schema = dict(schema)
    schema.pop("title", None)
    fields = {
        name: field
        for name, field in (schema.get("properties") or {}).items()
        if not _is_binary(field)
    }
    field_required = [
        name for name in schema.get("required") or [] if name in fields
    ]
    collision = any(name in properties for name in fields)
    if collision or not fields:
        key = param_name if param_name not in properties else f"{param_name}_body"
        nested = dict(schema)
        nested["type"] = "object"
        nested["properties"] = {
            name: _strip_title(field) for name, field in fields.items()
        }
        if field_required:
            nested["required"] = field_required
        properties[key] = nested
        locations[key] = f"{location}:object"
        if key not in required:
            required.append(key)
        return
    for name, field in fields.items():
        properties[name] = _strip_title(field)
        locations[name] = location
    for name in field_required:
        if name not in required:
            required.append(name)


def _output_schema(endpoint: Any, op_endpoint: Any) -> dict[str, Any] | None:
    model = _response_model(op_endpoint)
    if model is None and op_endpoint is not endpoint:
        model = _response_model(endpoint)
    if not model or model is False:
        return None
    try:
        from pydantic import TypeAdapter

        schema = TypeAdapter(model).json_schema()
    except Exception:
        logger.debug("Could not build an output schema for %r", model, exc_info=True)
        return None
    if not isinstance(schema, dict):
        return None
    if schema.get("type") != "object" and "properties" not in schema:
        return None
    schema.pop("title", None)
    return _retarget_refs(schema)


def _description(
    endpoint: Any,
    op_endpoint: Any,
    meta: dict[str, Any],
    method: str,
    path: str,
) -> tuple[str, str | None]:
    summary = meta.get("summary")
    explicit = meta.get("description")
    doc = inspect.getdoc(op_endpoint)
    if not doc:
        doc = inspect.getdoc(endpoint)
    description = explicit or doc or summary or f"{method} {path}"
    title = None
    if isinstance(summary, str) and summary and summary != description:
        title = summary
    return str(description).strip(), title


def _annotations(method: str) -> dict[str, bool]:
    verb = method.upper()
    return {
        "readOnlyHint": verb in {"GET", "HEAD"},
        "destructiveHint": verb == "DELETE",
        "idempotentHint": verb in {"GET", "HEAD", "PUT", "DELETE"},
        "openWorldHint": False,
    }


def _tool_methods(route: Any, endpoint: Any, dep_names: set[str]) -> list[str]:
    has_body = _body_parameter(endpoint, route, dep_names) is not None
    if not has_body and not isinstance(endpoint, type):
        has_body = any(
            spec.location in {"form", "file"} for spec in _marker_specs(endpoint)
        )
    return [method.upper() for method in _doc_methods(route, has_body=has_body)]


def _requires_upload(endpoint: Any) -> bool:
    if isinstance(endpoint, type):
        return False
    for spec in _marker_specs(endpoint):
        if spec.location == "file" and spec.required:
            return True
        if (
            spec.location == "form"
            and spec.required
            and _is_form_model(spec.annotation)
            and _model_requires_upload(spec.annotation)
        ):
            return True
    return False


def _model_requires_upload(model: Any) -> bool:
    try:
        from starlette.datastructures import UploadFile
    except ImportError:  # pragma: no cover - starlette is a core dependency
        return False
    from typing import get_args

    def contains(annotation: Any) -> bool:
        if isinstance(annotation, type) and issubclass(annotation, UploadFile):
            return True
        return any(contains(arg) for arg in get_args(annotation))

    try:
        return any(
            contains(field.annotation) and field.is_required()
            for field in model.model_fields.values()
        )
    except Exception:
        return False


def _framework_paths(api: Any, mcp_path: str) -> set[str]:
    paths = {mcp_path, mcp_path.rstrip("/")}
    openapi = getattr(api, "openapi", None)
    if openapi is not None:
        for attr in ("openapi_route", "docs_route"):
            value = getattr(openapi, attr, None)
            if isinstance(value, str) and value:
                paths.add(value)
                paths.add(value.rstrip("/"))
    for mounted in getattr(getattr(api, "router", None), "apps", {}) or {}:
        if isinstance(mounted, str) and mounted:
            paths.add(mounted)
            paths.add(mounted.rstrip("/") or "/")
    return paths


def _server_name(api: Any, explicit: str | None) -> str:
    if explicit:
        return explicit
    openapi = getattr(api, "openapi", None)
    title = getattr(openapi, "title", None) if openapi is not None else None
    if isinstance(title, str) and title:
        return title
    return "Responder"


def _server_version(api: Any) -> str:
    from responder.__version__ import __version__

    openapi = getattr(api, "openapi", None)
    version = getattr(openapi, "version", None) if openapi is not None else None
    if isinstance(version, str) and version and version != "0.0.0":
        return version
    return __version__


def _allocate_name(used: set[str], base: str, method: str) -> str:
    slug = _slug(base)
    if slug not in used:
        used.add(slug)
        return slug
    qualified = _slug(f"{slug}_{method.lower()}")
    if qualified not in used:
        used.add(qualified)
        return qualified
    index = 2
    while f"{qualified}_{index}" in used:
        index += 1
    name = f"{qualified}_{index}"
    used.add(name)
    return name


def _slug(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z_-]+", "_", value).strip("_")
    if not slug:
        slug = "tool"
    if slug[0].isdigit():
        slug = f"tool_{slug}"
    return slug


def _parameter_schema(parameter: dict[str, Any]) -> dict[str, Any]:
    schema = dict(parameter.get("schema") or {"type": "string"})
    description = parameter.get("description")
    if description and "description" not in schema:
        schema["description"] = description
    if parameter.get("deprecated"):
        schema["deprecated"] = True
    schema.pop("title", None)
    return schema


def _strip_title(schema: Any) -> Any:
    if isinstance(schema, dict):
        stripped = dict(schema)
        stripped.pop("title", None)
        return stripped
    return schema


def _is_binary(schema: Any) -> bool:
    return (
        isinstance(schema, dict)
        and schema.get("type") == "string"
        and schema.get("format") == "binary"
    )


def _merge_defs(
    base: dict[str, Any], extra: dict[str, Any], schema: dict[str, Any]
) -> None:
    for name, body in extra.items():
        if name not in base or base[name] == body:
            base[name] = body
            continue
        index = 2
        renamed = f"{name}_{index}"
        while renamed in base:
            index += 1
            renamed = f"{name}_{index}"
        base[renamed] = body
        _rename_ref(schema, f"#/$defs/{name}", f"#/$defs/{renamed}")
        _rename_ref(body, f"#/$defs/{name}", f"#/$defs/{renamed}")


def _rename_ref(obj: Any, old: str, new: str) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "$ref" and value == old:
                obj[key] = new
            else:
                _rename_ref(value, old, new)
    elif isinstance(obj, list):
        for item in obj:
            _rename_ref(item, old, new)


def _retarget_refs(obj: Any) -> Any:
    """Point OpenAPI component refs at this schema's own ``$defs``."""
    if isinstance(obj, dict):
        rewritten = {}
        for key, value in obj.items():
            if (
                key == "$ref"
                and isinstance(value, str)
                and value.startswith(_COMPONENT_REF)
            ):
                rewritten[key] = _LOCAL_REF + value[len(_COMPONENT_REF) :]
            else:
                rewritten[key] = _retarget_refs(value)
        return rewritten
    if isinstance(obj, list):
        return [_retarget_refs(item) for item in obj]
    return obj


def _origin_allowed(origin: str, host: str | None) -> bool:
    parsed = urllib.parse.urlsplit(origin)
    origin_host = (parsed.hostname or "").lower()
    if not origin_host:
        return False
    request_host = (host or "").strip().lower()
    if request_host.startswith("["):
        request_host = request_host.split("]", 1)[0].strip("[")
    elif request_host.count(":") == 1:
        request_host = request_host.split(":", 1)[0]
    if origin_host == request_host:
        return True
    return origin_host in _LOCAL_HOSTS and request_host in _LOCAL_HOSTS


def _accept_mode(header: str | None) -> str:
    """``json`` when a single JSON body is acceptable, else ``sse`` or ``none``."""
    if not header:
        return "json"
    types = [part.split(";", 1)[0].strip().lower() for part in header.split(",")]
    if any(item in {"application/json", "application/*", "*/*"} for item in types):
        return "json"
    if "text/event-stream" in types:
        return "sse"
    return "none"


def _method_not_allowed(resp: Any, method: str) -> None:
    resp.status_code = 405
    resp.headers["Allow"] = "POST"
    if method == "GET":
        message = "This server does not offer a server-initiated SSE stream"
    else:
        message = "Streamable HTTP requests use POST"
    resp.media = _error(None, INVALID_REQUEST, message)


def _send(resp: Any, payload: dict[str, Any], mode: str) -> None:
    if mode == "sse":
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        # JSON-RPC ``data`` is one line; a newline would split the SSE frame.
        data = data.replace("\n", "")
        resp.status_code = 200
        resp.headers["Content-Type"] = "text/event-stream"
        resp.headers["Cache-Control"] = "no-cache"
        resp.text = f"event: message\ndata: {data}\n\n"
        return
    resp.media = payload


def _error(rpc_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {"code": code, "message": message},
    }


def _result(rpc_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


async def _invoke_tool(
    api: Any, req: Any, tool: Tool, arguments: dict[str, Any]
) -> tuple[int, bytes, list[tuple[bytes, bytes]]]:
    path = _PATH_PARAM.sub(
        lambda match: _path_value(arguments, match.group(1)),
        tool.path_template,
    )
    mcp_path = getattr(api, "_mcp_path", None)
    if mcp_path and path.rstrip("/") == str(mcp_path).rstrip("/"):
        raise RuntimeError("MCP tool dispatch cannot call the MCP endpoint")

    query = _query_string(arguments, tool.locations)
    body, content_type = _request_body(arguments, tool.locations)
    scope = _incoming_scope(req)
    headers = _request_headers(scope, arguments, tool.locations, body, content_type)
    return await _call_app(api, scope, tool.method, path, query, headers, body)


def _path_value(arguments: dict[str, Any], name: str) -> str:
    if name not in arguments or arguments[name] is None:
        raise KeyError(name)
    return _scalar(arguments[name])


def _query_string(arguments: dict[str, Any], locations: dict[str, str]) -> bytes:
    pairs: list[tuple[str, str]] = []
    for name, location in locations.items():
        if location != "query" or name not in arguments or arguments[name] is None:
            continue
        value = arguments[name]
        if isinstance(value, (list, tuple)):
            pairs.extend((name, _scalar(item)) for item in value)
        else:
            pairs.append((name, _scalar(value)))
    if not pairs:
        return b""
    return urllib.parse.urlencode(pairs).encode("utf-8")


def _request_body(
    arguments: dict[str, Any], locations: dict[str, str]
) -> tuple[bytes, bytes | None]:
    for name, location in locations.items():
        if location == "body:object":
            if name not in arguments:
                raise KeyError(name)
            payload = arguments[name]
            if not isinstance(payload, dict):
                raise ValueError(f"Argument {name!r} must be an object")
            return (
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                b"application/json",
            )
        if location == "form:object":
            if name not in arguments:
                raise KeyError(name)
            payload = arguments[name]
            if not isinstance(payload, dict):
                raise ValueError(f"Argument {name!r} must be an object")
            return _form_bytes(payload), b"application/x-www-form-urlencoded"

    body = {
        name: arguments[name]
        for name, location in locations.items()
        if location == "body" and name in arguments
    }
    form = {
        name: arguments[name]
        for name, location in locations.items()
        if location == "form" and name in arguments
    }
    if body and form:
        raise ValueError(
            "This tool has both a JSON body and form fields; send one shape."
        )
    if body or any(location == "body" for location in locations.values()):
        return (
            json.dumps(body, ensure_ascii=False).encode("utf-8"),
            b"application/json",
        )
    if form:
        return _form_bytes(form), b"application/x-www-form-urlencoded"
    return b"", None


def _form_bytes(payload: dict[str, Any]) -> bytes:
    pairs: list[tuple[str, str]] = []
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            pairs.extend((key, _scalar(item)) for item in value)
        elif isinstance(value, dict):
            pairs.append((key, json.dumps(value, separators=(",", ":"))))
        else:
            pairs.append((key, _scalar(value)))
    return urllib.parse.urlencode(pairs).encode("utf-8")


def _incoming_scope(req: Any) -> dict[str, Any]:
    """The ASGI scope behind a Responder request."""
    starlette = getattr(req, "_starlette", None)
    if starlette is not None:
        return starlette.scope
    return req.scope


def _request_headers(
    scope: dict[str, Any],
    arguments: dict[str, Any],
    locations: dict[str, str],
    body: bytes,
    content_type: bytes | None,
) -> list[tuple[bytes, bytes]]:
    merged: dict[bytes, bytes] = {}
    for key, value in scope.get("headers") or []:
        lowered = key.lower()
        if lowered not in _DROP_HEADERS:
            merged[lowered] = value
    cookies: dict[str, str] = {}
    for name, location in locations.items():
        if name not in arguments or arguments[name] is None:
            continue
        if location == "header":
            merged[name.lower().encode("utf-8")] = _scalar(arguments[name]).encode(
                "utf-8"
            )
        elif location == "cookie":
            cookies[name] = _scalar(arguments[name])
    if cookies:
        merged[b"cookie"] = _merge_cookie(merged.get(b"cookie"), cookies)
    merged.pop(b"content-type", None)
    merged[b"content-length"] = str(len(body)).encode("ascii")
    if content_type is not None:
        merged[b"content-type"] = content_type
    return list(merged.items())


def _merge_cookie(existing: bytes | None, updates: dict[str, str]) -> bytes:
    jar: dict[str, str] = {}
    if existing:
        for part in existing.decode("latin-1", "replace").split(";"):
            name, sep, value = part.partition("=")
            if sep:
                jar[name.strip()] = value.strip()
    jar.update(updates)
    return "; ".join(f"{name}={value}" for name, value in jar.items()).encode("utf-8")


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


async def _call_app(
    api: Any,
    incoming: dict[str, Any],
    method: str,
    path: str,
    query: bytes,
    headers: list[tuple[bytes, bytes]],
    body: bytes,
) -> tuple[int, bytes, list[tuple[bytes, bytes]]]:
    """Run one request through the app so middleware and hooks still apply."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": incoming.get("scheme", "http"),
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query,
        "headers": headers,
        "client": incoming.get("client"),
        "server": incoming.get("server"),
        "root_path": "",
        "state": {},
    }
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    status = 500
    response_headers: list[tuple[bytes, bytes]] = []
    chunks: list[bytes] = []

    async def send(message: dict[str, Any]) -> None:
        nonlocal status, response_headers
        if message["type"] == "http.response.start":
            status = int(message["status"])
            response_headers = list(message.get("headers") or [])
        elif message["type"] == "http.response.body":
            chunk = message.get("body") or b""
            if chunk:
                chunks.append(chunk)

    await api(scope, receive, send)
    return status, b"".join(chunks), response_headers


def _tool_result(
    status: int, raw: bytes, headers: list[tuple[bytes, bytes]]
) -> dict[str, Any]:
    content_type = ""
    for key, value in headers:
        if key.lower() == b"content-type":
            content_type = value.decode("latin-1", "replace")
            break
    text = raw.decode("utf-8", "replace")
    structured: Any = None
    if text and ("json" in content_type or text[:1] in "{["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            text = json.dumps(parsed, indent=2, ensure_ascii=False)
            if isinstance(parsed, dict):
                structured = parsed
    is_error = status >= 400
    if is_error:
        text = f"HTTP {status}\n{text}" if text else f"HTTP {status}"
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }
    if structured is not None:
        result["structuredContent"] = structured
    return result
