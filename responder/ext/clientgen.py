"""Generate small clients from a Responder OpenAPI schema.

The generator is intentionally dependency-light: emitted Python/Ruby/PHP clients
use standard libraries, JavaScript/TypeScript clients use ``fetch``, and Python
also accepts a TestClient/httpx-style ``session`` for in-process tests.
"""

from __future__ import annotations

import json
import keyword
import re
from pathlib import Path
from typing import Any

import yaml

__all__ = ["generate_client", "write_client"]

_IDENT_RE = re.compile(r"[^0-9a-zA-Z_]+")
_PATH_PARAM_RE = re.compile(r"{([^}:]+)(?::[^}]+)?}")
_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}
_LANGUAGES = {"python", "javascript", "typescript", "ruby", "php"}


def _load_spec(source: Any) -> dict[str, Any]:
    """Load an OpenAPI spec from an app, OpenAPISchema, dict, or YAML string."""
    if isinstance(source, dict):
        return source
    if isinstance(source, str):
        loaded = yaml.safe_load(source)
        if not isinstance(loaded, dict):
            raise TypeError("OpenAPI YAML did not parse to an object")
        return loaded
    if hasattr(source, "openapi"):
        return _load_spec(source.openapi)
    if hasattr(source, "_apispec"):
        return source._apispec.to_dict()
    raise TypeError("Expected an API/OpenAPISchema, OpenAPI dict, or YAML string")


def _identifier(name: str, *, fallback: str = "value") -> str:
    """Return a safe Python identifier for ``name``."""
    cleaned = _IDENT_RE.sub("_", name).strip("_").lower()
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"{fallback}_{cleaned}"
    if keyword.iskeyword(cleaned):
        cleaned += "_"
    return cleaned


def _camel_identifier(name: str, *, fallback: str = "value") -> str:
    """Return a lowerCamelCase identifier for JS/TS."""
    parts = [_identifier(part) for part in re.split(r"[^0-9a-zA-Z]+", name) if part]
    if not parts:
        parts = [fallback]
    result = parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])
    if result[0].isdigit():
        result = f"{fallback}{result[:1].upper()}{result[1:]}"
    if result in {"class", "default", "function", "return", "new", "delete"}:
        result += "_"
    return result


def _php_identifier(name: str, *, fallback: str = "value") -> str:
    ident = _identifier(name, fallback=fallback)
    if ident in {"class", "function", "public", "private", "protected"}:
        ident += "_"
    return ident


def _class_name(name: str) -> str:
    if name.isidentifier() and not keyword.iskeyword(name) and not name[0].isdigit():
        return name
    parts = [_identifier(part) for part in re.split(r"[^0-9a-zA-Z]+", name) if part]
    result = "".join(part[:1].upper() + part[1:] for part in parts)
    if not result or result[0].isdigit():
        result = "APIClient"
    return result


def _component_type_names(spec: dict[str, Any]) -> dict[str, str]:
    """Return stable generated type names for component schemas."""
    schemas = ((spec.get("components") or {}).get("schemas") or {})
    names = {}
    used: set[str] = set()
    for raw_name in sorted(schemas):
        base = _class_name(str(raw_name))
        name = base
        index = 2
        while name in used:
            name = f"{base}{index}"
            index += 1
        used.add(name)
        names[str(raw_name)] = name
    return names


def _ref_component_name(ref: str) -> str | None:
    prefix = "#/components/schemas/"
    if ref.startswith(prefix):
        return ref.removeprefix(prefix)
    return None


def _ref_type_name(schema: dict[str, Any], type_names: dict[str, str]) -> str | None:
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return None
    name = _ref_component_name(ref)
    if name is None:
        return None
    return type_names.get(name)


def _operation_name(method: str, path: str, operation: dict[str, Any], used: set) -> str:
    raw = operation.get("operationId")
    if raw:
        base = _identifier(str(raw), fallback=f"{method}_request")
    else:
        pieces = [_identifier(part) for part in re.split(r"[/{}:.-]+", path) if part]
        base = "_".join([method, *pieces]) if pieces else method
    name = base
    index = 2
    while name in used:
        name = f"{base}_{index}"
        index += 1
    used.add(name)
    return name


def _schema_type(
    schema: dict[str, Any] | None, type_names: dict[str, str] | None = None
) -> str:
    """Translate a small OpenAPI schema fragment to a Python type string."""
    if not schema:
        return "Any"
    type_names = type_names or {}
    if "$ref" in schema:
        return _ref_type_name(schema, type_names) or "dict[str, Any]"
    if "allOf" in schema:
        variants = schema.get("allOf") or []
        if len(variants) == 1:
            return _schema_type(variants[0], type_names)
        return "Any"
    if "anyOf" in schema or "oneOf" in schema:
        variants = schema.get("anyOf") or schema.get("oneOf") or []
        non_null = [v for v in variants if v != {"type": "null"}]
        if len(non_null) == 1 and len(non_null) != len(variants):
            return f"{_schema_type(non_null[0], type_names)} | None"
        return "Any"
    typ = schema.get("type")
    if typ == "integer":
        result = "int"
    elif typ == "number":
        result = "float"
    elif typ == "boolean":
        result = "bool"
    elif typ == "array":
        result = f"list[{_schema_type(schema.get('items'), type_names)}]"
    elif typ == "object":
        result = "dict[str, Any]"
    else:
        result = "str"
    if schema.get("nullable") and " | None" not in result:
        return f"{result} | None"
    return result


def _ts_schema_type(
    schema: dict[str, Any] | None, type_names: dict[str, str] | None = None
) -> str:
    """Translate a small OpenAPI schema fragment to TypeScript."""
    if not schema:
        return "unknown"
    type_names = type_names or {}
    if "$ref" in schema:
        return _ref_type_name(schema, type_names) or "Record<string, unknown>"
    if "allOf" in schema:
        variants = schema.get("allOf") or []
        if len(variants) == 1:
            return _ts_schema_type(variants[0], type_names)
        return "unknown"
    if "anyOf" in schema or "oneOf" in schema:
        variants = schema.get("anyOf") or schema.get("oneOf") or []
        non_null = [v for v in variants if v != {"type": "null"}]
        if len(non_null) == 1 and len(non_null) != len(variants):
            return f"{_ts_schema_type(non_null[0], type_names)} | null"
        return "unknown"
    typ = schema.get("type")
    if typ in ("integer", "number"):
        result = "number"
    elif typ == "boolean":
        result = "boolean"
    elif typ == "array":
        result = f"Array<{_ts_schema_type(schema.get('items'), type_names)}>"
    elif typ == "object":
        result = "Record<string, unknown>"
    else:
        result = "string"
    if schema.get("nullable") and "null" not in result:
        return f"{result} | null"
    return result


def _param_default(param: dict[str, Any]) -> str | None:
    schema = param.get("schema") or {}
    if "default" in schema:
        return repr(schema["default"])
    if not param.get("required", False):
        return "None"
    return None


def _js_param_default(param: dict[str, Any]) -> str | None:
    schema = param.get("schema") or {}
    if "default" in schema:
        return json.dumps(schema["default"])
    if not param.get("required", False):
        return "null"
    return None


def _path_params(path: str) -> set[str]:
    return set(_PATH_PARAM_RE.findall(path))


def _request_body_schema(operation: dict[str, Any]) -> dict[str, Any] | None:
    body = operation.get("requestBody")
    if not isinstance(body, dict):
        return None
    content = body.get("content") or {}
    media: Any
    media = content.get("application/json") or next(iter(content.values()), {})
    schema = media.get("schema") if isinstance(media, dict) else None
    if isinstance(schema, dict):
        return schema
    return None


def _request_body_media_type(operation: dict[str, Any]) -> str | None:
    """The requestBody media type the client should send (JSON preferred)."""
    body = operation.get("requestBody")
    if not isinstance(body, dict):
        return None
    content = body.get("content") or {}
    if "application/json" in content:
        return "application/json"
    return next(iter(content), None)


def _request_body_kind(operation: dict[str, Any]) -> str:
    """How the request body must be encoded: ``json``, ``form``, or ``multipart``."""
    media = (_request_body_media_type(operation) or "").split(";")[0].strip().lower()
    if media == "application/x-www-form-urlencoded":
        return "form"
    if media == "multipart/form-data":
        return "multipart"
    return "json"


def _request_body_type(
    operation: dict[str, Any], type_names: dict[str, str] | None = None
) -> str | None:
    schema = _request_body_schema(operation)
    if isinstance(schema, dict):
        return _schema_type(schema, type_names)
    body = operation.get("requestBody")
    if not isinstance(body, dict):
        return None
    return "Mapping[str, Any]"


def _ts_request_body_type(
    operation: dict[str, Any], type_names: dict[str, str] | None = None
) -> str | None:
    schema = _request_body_schema(operation)
    if isinstance(schema, dict):
        return _ts_schema_type(schema, type_names)
    body = operation.get("requestBody")
    if not isinstance(body, dict):
        return None
    return "Record<string, unknown>"


def _response_schema(operation: dict[str, Any]) -> dict[str, Any] | None:
    responses = operation.get("responses") or {}
    for status, response in sorted(responses.items()):
        if not str(status).startswith("2") or not isinstance(response, dict):
            continue
        content = response.get("content") or {}
        media: Any
        media = content.get("application/json") or next(iter(content.values()), {})
        schema = media.get("schema") if isinstance(media, dict) else None
        if isinstance(schema, dict):
            return schema
    return None


def _response_type(operation: dict[str, Any], type_names: dict[str, str]) -> str:
    schema = _response_schema(operation)
    if isinstance(schema, dict):
        return _schema_type(schema, type_names)
    return "Any"


def _ts_response_type(operation: dict[str, Any], type_names: dict[str, str]) -> str:
    schema = _response_schema(operation)
    if isinstance(schema, dict):
        return _ts_schema_type(schema, type_names)
    return "unknown"


def _schema_py_literal(schema: dict[str, Any] | None) -> str:
    return "None" if schema is None else repr(schema)


def _schema_js_literal(schema: dict[str, Any] | None) -> str:
    return "null" if schema is None else json.dumps(schema, sort_keys=True)


def _component_schema_literals(spec: dict[str, Any]) -> tuple[str, str]:
    schemas = ((spec.get("components") or {}).get("schemas") or {})
    py_schemas = {str(name): schema for name, schema in schemas.items()}
    return repr(py_schemas), json.dumps(py_schemas, sort_keys=True)


def _operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any], str]]:
    """Return ``(method, path, operation, generated_name)`` entries."""
    operations = []
    used: set[str] = set()
    for path, item in sorted((spec.get("paths") or {}).items()):
        if not isinstance(item, dict):
            continue
        for method, operation in sorted(item.items()):
            if method not in _METHODS or not isinstance(operation, dict):
                continue
            name = _operation_name(method, path, operation, used)
            operations.append((method, path, operation, name))
    return operations


def _typed_dict_source(
    name: str, schema: dict[str, Any], type_names: dict[str, str]
) -> str:
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        properties = {}
    required = set(schema.get("required") or [])
    total = "False" if properties and set(properties) != required else "True"
    valid_keys = all(
        str(key).isidentifier() and not keyword.iskeyword(str(key))
        for key in properties
    )
    if not properties:
        return f"class {name}(TypedDict):\n    pass\n"
    if valid_keys:
        suffix = "" if total == "True" else ", total=False"
        lines = [f"class {name}(TypedDict{suffix}):"]
        for prop_name, prop_schema in sorted(properties.items()):
            typ = _schema_type(prop_schema, type_names)
            lines.append(f"    {prop_name}: {typ}")
        return "\n".join(lines) + "\n"
    fields = ", ".join(
        f"{str(prop_name)!r}: {_schema_type(prop_schema, type_names)}"
        for prop_name, prop_schema in sorted(properties.items())
    )
    return f"{name} = TypedDict({name!r}, {{{fields}}}, total={total})\n"


def _python_type_defs(spec: dict[str, Any], type_names: dict[str, str]) -> str:
    schemas = ((spec.get("components") or {}).get("schemas") or {})
    blocks = []
    for raw_name, schema in sorted(schemas.items()):
        if not isinstance(schema, dict):
            continue
        name = type_names.get(str(raw_name))
        if name is None:
            continue
        if schema.get("type") == "object" or "properties" in schema:
            blocks.append(_typed_dict_source(name, schema, type_names))
        else:
            blocks.append(f"{name} = {_schema_type(schema, type_names)}\n")
    return "\n".join(blocks) + ("\n" if blocks else "")


def _ts_property_name(name: str) -> str:
    if re.match(r"^[A-Za-z_$][0-9A-Za-z_$]*$", name):
        return name
    return json.dumps(name)


def _ts_type_def_source(
    name: str, schema: dict[str, Any], type_names: dict[str, str]
) -> str:
    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        properties = {}
    if schema.get("type") == "object" or "properties" in schema:
        if not properties:
            return f"export interface {name} {{\n  [key: string]: unknown;\n}}\n"
        required = set(schema.get("required") or [])
        lines = [f"export interface {name} {{"]
        for prop_name, prop_schema in sorted(properties.items()):
            optional = "" if prop_name in required else "?"
            typ = _ts_schema_type(prop_schema, type_names)
            lines.append(f"  {_ts_property_name(str(prop_name))}{optional}: {typ};")
        lines.append("}")
        return "\n".join(lines) + "\n"
    return f"export type {name} = {_ts_schema_type(schema, type_names)};\n"


def _ts_type_defs(spec: dict[str, Any], type_names: dict[str, str]) -> str:
    schemas = ((spec.get("components") or {}).get("schemas") or {})
    blocks = []
    for raw_name, schema in sorted(schemas.items()):
        if not isinstance(schema, dict):
            continue
        name = type_names.get(str(raw_name))
        if name is not None:
            blocks.append(_ts_type_def_source(name, schema, type_names))
    return "\n".join(blocks) + ("\n" if blocks else "")


def _method_source(
    method: str,
    path: str,
    operation: dict[str, Any],
    name: str,
    type_names: dict[str, str],
) -> str:
    path_names = _path_params(path)
    parameters = operation.get("parameters") or []
    required_parts = []
    optional_parts = []
    query_pairs = []
    header_pairs = []
    cookie_pairs = []
    path_name_map: dict[str, str] = {}
    # Names the method body relies on must never be shadowed by a parameter.
    seen_python_names = {"self", "body", "path"}

    for param in parameters:
        if not isinstance(param, dict):
            continue
        raw_name = str(param.get("name", "value"))
        py_name = _identifier(raw_name)
        while py_name in seen_python_names:
            py_name += "_"
        seen_python_names.add(py_name)
        schema = param.get("schema") or {}
        default = _param_default(param)
        annotation = _schema_type(schema, type_names)
        if default == "None" and " | None" not in annotation:
            annotation = f"{annotation} | None"
        part = f"{py_name}: {annotation}"
        if default is not None:
            part += f" = {default}"
            optional_parts.append(part)
        else:
            required_parts.append(part)

        where = param.get("in")
        if where == "query":
            query_pairs.append((raw_name, py_name))
        elif where == "header":
            header_pairs.append((raw_name, py_name))
        elif where == "cookie":
            cookie_pairs.append((raw_name, py_name))
        elif where == "path":
            path_name_map[raw_name] = py_name

    body_type = _request_body_type(operation, type_names)
    body_required = bool((operation.get("requestBody") or {}).get("required"))
    if body_type is not None:
        if body_required:
            required_parts.append(f"body: {body_type}")
        else:
            optional_parts.append(f"body: {body_type} | None = None")

    signature = ", ".join(["self", *required_parts, *optional_parts])
    path_expr = repr(path)
    for raw_name in sorted(path_names, key=len, reverse=True):
        py_name = path_name_map.get(raw_name, _identifier(raw_name))
        placeholder = repr("{" + raw_name + "}")
        path_expr += f".replace({placeholder}, _quote({py_name}))"

    query_expr = "{" + ", ".join(f"{raw!r}: {py}" for raw, py in query_pairs) + "}"
    extra_args = ""
    if header_pairs:
        header_expr = ", ".join(f"{raw!r}: {py}" for raw, py in header_pairs)
        extra_args += f", headers={{{header_expr}}}"
    if cookie_pairs:
        cookie_expr = ", ".join(f"{raw!r}: {py}" for raw, py in cookie_pairs)
        extra_args += f", cookies={{{cookie_expr}}}"
    body_arg = {
        "json": "json_body",
        "form": "form_body",
        "multipart": "multipart_body",
    }[_request_body_kind(operation)]
    body_expr = "body" if body_type is not None else "None"
    return_type = _response_type(operation, type_names)
    request_schema = _schema_py_literal(_request_body_schema(operation))
    response_schema = _schema_py_literal(_response_schema(operation))
    return (
        f"    def {name}({signature}) -> {return_type}:\n"
        f"        path = {path_expr}\n"
        f"        return self._request({method.upper()!r}, path, "
        f"query={query_expr}{extra_args}, {body_arg}={body_expr}, "
        f"request_schema={request_schema}, response_schema={response_schema})\n"
    )


def _js_string(value: str) -> str:
    return repr(value)


def _php_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _ruby_string(value: str) -> str:
    """A Ruby single-quoted string literal (no ``#{}`` interpolation, unlike
    double-quoted), with the only two meaningful escapes applied."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _js_method_source(
    method: str,
    path: str,
    operation: dict[str, Any],
    name: str,
    *,
    typed: bool = False,
    type_names: dict[str, str] | None = None,
) -> str:
    path_names = _path_params(path)
    parameters = operation.get("parameters") or []
    required_parts = []
    optional_parts = []
    query_pairs = []
    header_pairs = []
    cookie_pairs = []
    path_name_map: dict[str, str] = {}
    # Names the method body relies on must never be shadowed by a parameter:
    # `body` and the `path` const are declared in the method scope, and
    # `quote` is the module-level URL-encoding helper the path template calls.
    # (Python mangles `_quote` out of reach; Ruby's `quote(...)` and PHP's
    # `$this->quote(...)` cannot be shadowed by a parameter.)
    seen_names = {"body", "path", "quote"}

    for param in parameters:
        if not isinstance(param, dict):
            continue
        raw_name = str(param.get("name", "value"))
        js_name = _camel_identifier(raw_name)
        while js_name in seen_names:
            js_name += "_"
        seen_names.add(js_name)
        default = _js_param_default(param)
        schema = param.get("schema") or {}
        if typed:
            annotation = _ts_schema_type(schema, type_names)
            if default == "null" and "null" not in annotation:
                annotation = f"{annotation} | null"
            part = f"{js_name}: {annotation}"
        else:
            part = js_name
        if default is not None:
            part += f" = {default}"
            optional_parts.append(part)
        else:
            required_parts.append(part)
        where = param.get("in")
        if where == "query":
            query_pairs.append((raw_name, js_name))
        elif where == "header":
            header_pairs.append((raw_name, js_name))
        elif where == "cookie":
            cookie_pairs.append((raw_name, js_name))
        elif where == "path":
            path_name_map[raw_name] = js_name

    if typed:
        body_type = _ts_request_body_type(operation, type_names)
    else:
        body_type = _request_body_type(operation)
    body_required = bool((operation.get("requestBody") or {}).get("required"))
    if body_type is not None:
        if typed:
            if body_required:
                part = f"body: {body_type}"
            else:
                part = f"body: {body_type} | null = null"
        else:
            part = "body" if body_required else "body = null"
        (required_parts if body_required else optional_parts).append(part)

    signature = ", ".join([*required_parts, *optional_parts])
    path_expr = _js_string(path)
    for raw_name in sorted(path_names, key=len, reverse=True):
        js_name = path_name_map.get(raw_name, _camel_identifier(raw_name))
        placeholder = _js_string("{" + raw_name + "}")
        path_expr += f".replace({placeholder}, quote({js_name}))"
    query_expr = (
        "{"
        + ", ".join(f"{_js_string(raw)}: {js_name}" for raw, js_name in query_pairs)
        + "}"
    )
    extra_options = ""
    if header_pairs:
        header_expr = ", ".join(
            f"{_js_string(raw)}: {js_name}" for raw, js_name in header_pairs
        )
        extra_options += f", headers: {{{header_expr}}}"
    if cookie_pairs:
        cookie_expr = ", ".join(
            f"{_js_string(raw)}: {js_name}" for raw, js_name in cookie_pairs
        )
        extra_options += f", cookies: {{{cookie_expr}}}"
    body_option = {
        "json": "body",
        "form": "formBody",
        "multipart": "multipartBody",
    }[_request_body_kind(operation)]
    body_expr = "body" if body_type is not None else "null"
    response_type = _ts_response_type(operation, type_names or {}) if typed else ""
    return_type = f": Promise<{response_type}>" if typed else ""
    request_schema = _schema_js_literal(_request_body_schema(operation))
    response_schema = _schema_js_literal(_response_schema(operation))
    request_call = (
        f"this.request({_js_string(method.upper())}, path, "
        f"{{ query: {query_expr}{extra_options}, {body_option}: {body_expr}, "
        f"requestSchema: {request_schema}, responseSchema: {response_schema} }})"
    )
    if typed:
        request_call = f"{request_call} as Promise<{response_type}>"
    return (
        f"  {name}({signature}){return_type} {{\n"
        f"    const path = {path_expr};\n"
        f"    return {request_call};\n"
        f"  }}\n"
    )


def _generate_javascript(
    operations: list[tuple[str, str, dict[str, Any], str]],
    class_name: str,
    *,
    typed: bool = False,
    type_names: dict[str, str] | None = None,
    type_defs: str = "",
    component_schemas: str = "{}",
) -> str:
    type_names = type_names or {}
    methods = "\n".join(
        _js_method_source(
            method, path, operation, name, typed=typed, type_names=type_names
        )
        for method, path, operation, name in operations
    )
    if not methods:
        methods = "  // No operations were found in the OpenAPI schema.\n"
    export = "export "
    type_bits = """
type HeadersMap = Record<string, string>;
type Schema = Record<string, any>;
type RequestOptions = {
  query?: Record<string, unknown>;
  headers?: Record<string, unknown>;
  cookies?: Record<string, unknown>;
  body?: unknown;
  formBody?: Record<string, unknown> | null;
  multipartBody?: Record<string, unknown> | null;
  requestSchema?: Schema | null;
  responseSchema?: Schema | null;
};
type FetchFunction = typeof fetch;

""" if typed else ""
    has_problem_type = "ProblemDetails" in (type_names or {}).values()
    problem_type = "" if has_problem_type else """export type ProblemDetails = {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  instance?: string;
  request_id?: string;
  errors?: Array<Record<string, unknown>>;
  [key: string]: unknown;
};

""" if typed else ""
    api_error_field_bits = """  statusCode: number;
  body: unknown;
  problem: ProblemDetails | null;

""" if typed else ""
    api_validation_field_bits = """  path: string;
  expected: string;
  value: unknown;

""" if typed else ""
    field_bits = """  baseUrl: string;
  headers: HeadersMap;
  fetchImpl: FetchFunction;
  validate: boolean;

""" if typed else ""
    ctor_sig = (
        "baseUrl = '', { headers = {}, bearerToken = null, basicAuth = null, "
        "apiKey = null, fetchImpl = fetch, validate = false } = {}"
    )
    if typed:
        ctor_sig = (
            "baseUrl: string = '', { headers = {}, bearerToken = null, "
            "basicAuth = null, apiKey = null, fetchImpl = fetch, "
            "validate = false }: { "
            "headers?: HeadersMap; bearerToken?: string | null; "
            "basicAuth?: [string, string] | null; apiKey?: [string, string] | null; "
            "fetchImpl?: FetchFunction; validate?: boolean } = {}"
        )
    request_sig = (
        "async request(method, path, { query = {}, headers = {}, cookies = {}, "
        "body = null, formBody = null, multipartBody = null, "
        "requestSchema = null, responseSchema = null } = {})"
    )
    if typed:
        request_sig = (
            "async request(method: string, path: string, "
            "{ query = {}, headers = {}, cookies = {}, body = null, "
            "formBody = null, multipartBody = null, requestSchema = null, "
            "responseSchema = null }: RequestOptions = {}): Promise<unknown>"
        )
    encode_sig = "(value: string): string" if typed else "(value)"
    buffer_ctor = (
        "(globalThis as unknown as { Buffer?: { from(value: string, "
        "encoding: string): { toString(encoding: string): string } } }).Buffer"
        if typed
        else "globalThis.Buffer"
    )
    init_type = ": RequestInit" if typed else ""
    quote_type = ": unknown" if typed else ""
    status_arg = f"statusCode{': number' if typed else ''}"
    body_arg = f"body{': unknown' if typed else ''}"
    validation_args = (
        "message: string, path: string, expected: string, value: unknown"
        if typed
        else "message, path, expected, value"
    )
    quote_decl = (
        f"const quote = (value{quote_type}) => "
        "encodeURIComponent(String(value));"
    )
    # Type annotations for the schema-validation helpers (TS only) so the
    # emitted client passes `tsc`/`deno check` under noImplicitAny.
    a_value = ": any" if typed else ""
    a_schema = ": any" if typed else ""
    a_path = ": string" if typed else ""
    a_expected = ": string" if typed else ""
    a_variant = ": any" if typed else ""
    problem_guard_return = ": value is ProblemDetails" if typed else ""
    schemas_type = ": Record<string, any>" if typed else ""
    schema_decl = f"const SCHEMAS{schemas_type} = {component_schemas};"
    return f"""{type_bits}{problem_type}{type_defs}{schema_decl}

{quote_decl}

const encodeBase64 = {encode_sig} => {{
  if (typeof btoa === 'function') return btoa(value);
  const bufferCtor = {buffer_ctor};
  if (bufferCtor) return bufferCtor.from(value, 'utf8').toString('base64');
  throw new Error('No base64 encoder is available');
}};

export class APIError extends Error {{
{api_error_field_bits}  constructor({status_arg}, {body_arg}) {{
    const problem = isProblemDetails(body) ? body : null;
    super(problem?.title || `API request failed with status ${{statusCode}}`);
    this.statusCode = statusCode;
    this.body = body;
    this.problem = problem;
  }}
}}

export class APIValidationError extends Error {{
{api_validation_field_bits}  constructor({validation_args}) {{
    super(message);
    this.path = path;
    this.expected = expected;
    this.value = value;
  }}
}}

const resolveSchema = (schema{a_schema}) => {{
  if (!schema || !schema.$ref) return schema;
  const prefix = '#/components/schemas/';
  if (!schema.$ref.startsWith(prefix)) return schema;
  return SCHEMAS[schema.$ref.slice(prefix.length)] || schema;
}};

const isProblemDetails = (value{a_value}){problem_guard_return} => (
  value !== null
  && typeof value === 'object'
  && typeof value.status === 'number'
  && typeof value.title === 'string'
);

const validationError = (path{a_path}, expected{a_expected}, value{a_value}) => (
  new APIValidationError(
    `${{path}} expected ${{expected}}`,
    path,
    expected,
    value,
  )
);

const validateValue = (value{a_value}, schema{a_schema}, path{a_path} = 'value') => {{
  schema = resolveSchema(schema);
  if (!schema) return;
  if (schema.nullable && value === null) return;
  if (schema.anyOf || schema.oneOf) {{
    const variants = schema.anyOf || schema.oneOf;
    const matched = variants.some((variant{a_variant}) => {{
      try {{
        validateValue(value, variant, path);
        return true;
      }} catch (_error) {{
        return false;
      }}
    }});
    if (matched) return;
    throw validationError(path, 'one allowed schema', value);
  }}
  if (schema.allOf) {{
    for (const variant of schema.allOf) validateValue(value, variant, path);
    return;
  }}
  if (!schema.type) return;
  if (schema.type === 'integer') {{
    if (!Number.isInteger(value)) throw validationError(path, 'integer', value);
    return;
  }}
  if (schema.type === 'number') {{
    if (typeof value !== 'number') throw validationError(path, 'number', value);
    return;
  }}
  if (schema.type === 'boolean') {{
    if (typeof value !== 'boolean') throw validationError(path, 'boolean', value);
    return;
  }}
  if (schema.type === 'string') {{
    if (typeof value !== 'string') throw validationError(path, 'string', value);
    return;
  }}
  if (schema.type === 'array') {{
    if (!Array.isArray(value)) throw validationError(path, 'array', value);
    value.forEach((item, index) => {{
      validateValue(item, schema.items || {{}}, `${{path}}[${{index}}]`);
    }});
    return;
  }}
  if (schema.type === 'object') {{
    if (value === null || typeof value !== 'object' || Array.isArray(value)) {{
      throw validationError(path, 'object', value);
    }}
    for (const key of schema.required || []) {{
      if (!(key in value)) {{
        throw validationError(`${{path}}.${{key}}`, 'present', undefined);
      }}
    }}
    for (const [key, propSchema] of Object.entries(schema.properties || {{}})) {{
      if (value[key] !== undefined) {{
        validateValue(value[key], propSchema, `${{path}}.${{key}}`);
      }}
    }}
  }}
}};

{export}class {class_name} {{
{field_bits}  constructor({ctor_sig}) {{
    this.baseUrl = baseUrl.replace(/\\/$/, '');
    this.headers = {{ ...headers }};
    this.fetchImpl = fetchImpl;
    this.validate = validate;
    if (bearerToken !== null) this.headers.Authorization = `Bearer ${{bearerToken}}`;
    if (basicAuth !== null) {{
      const [user, password] = basicAuth;
      this.headers.Authorization = `Basic ${{encodeBase64(`${{user}}:${{password}}`)}}`;
    }}
    if (apiKey !== null) {{
      const [name, value] = apiKey;
      this.headers[name] = value;
    }}
  }}

  {request_sig} {{
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query || {{}})) {{
      if (value === null || value === undefined) continue;
      if (Array.isArray(value)) {{
        for (const item of value) params.append(key, String(item));
      }} else {{
        params.append(key, String(value));
      }}
    }}
    const qs = params.toString();
    const url = `${{this.baseUrl}}${{path}}${{qs ? `?${{qs}}` : ''}}`;
    const requestHeaders = {{ ...this.headers }};
    for (const [key, value] of Object.entries(headers || {{}})) {{
      if (value !== null && value !== undefined) requestHeaders[key] = String(value);
    }}
    const cookiePairs = Object.entries(cookies || {{}})
      .filter(([, value]) => value !== null && value !== undefined)
      .map(([key, value]) => `${{key}}=${{value}}`);
    if (cookiePairs.length) {{
      const cookie = cookiePairs.join('; ');
      requestHeaders.Cookie = requestHeaders.Cookie
        ? `${{requestHeaders.Cookie}}; ${{cookie}}`
        : cookie;
    }}
    const init{init_type} = {{ method, headers: requestHeaders }};
    if (formBody !== null && formBody !== undefined) {{
      if (this.validate && requestSchema) {{
        validateValue(formBody, requestSchema, 'body');
      }}
      const form = new URLSearchParams();
      for (const [key, value] of Object.entries(formBody)) {{
        if (value === null || value === undefined) continue;
        if (Array.isArray(value)) {{
          for (const item of value) form.append(key, String(item));
        }} else {{
          form.append(key, String(value));
        }}
      }}
      requestHeaders['Content-Type'] = 'application/x-www-form-urlencoded';
      init.body = form.toString();
    }} else if (multipartBody !== null && multipartBody !== undefined) {{
      const form = new FormData();
      for (const [key, value] of Object.entries(multipartBody)) {{
        if (value === null || value === undefined) continue;
        const items = Array.isArray(value) ? value : [value];
        for (const item of items) {{
          form.append(key, item instanceof Blob ? item : String(item));
        }}
      }}
      // fetch supplies the multipart Content-Type (with boundary) itself.
      delete requestHeaders['Content-Type'];
      init.body = form;
    }} else if (body !== null && body !== undefined) {{
      if (this.validate && requestSchema) validateValue(body, requestSchema, 'body');
      requestHeaders['Content-Type'] =
        requestHeaders['Content-Type'] || 'application/json';
      init.body = JSON.stringify(body);
    }}
    const response = await this.fetchImpl(url, init);
    const contentType = response.headers.get('content-type') || '';
    const text = await response.text();
    const payload = text && contentType.includes('json')
      ? JSON.parse(text)
      : text || null;
    if (!response.ok) throw new APIError(response.status, payload);
    if (this.validate && responseSchema) {{
      validateValue(payload, responseSchema, 'response');
    }}
    return payload;
  }}

{methods}}}
"""


def _ruby_method_source(
    method: str, path: str, operation: dict[str, Any], name: str
) -> str:
    path_names = _path_params(path)
    parameters = operation.get("parameters") or []
    required_parts = []
    optional_parts = []
    query_pairs = []
    header_pairs = []
    cookie_pairs = []
    path_name_map: dict[str, str] = {}
    # Names the method body relies on must never be shadowed by a parameter.
    seen_names = {"body", "path"}

    for param in parameters:
        if not isinstance(param, dict):
            continue
        raw_name = str(param.get("name", "value"))
        rb_name = _identifier(raw_name)
        while rb_name in seen_names:
            rb_name += "_"
        seen_names.add(rb_name)
        default = _param_default(param)
        if default is None:
            required_parts.append(rb_name)
        else:
            optional_parts.append(f"{rb_name}: nil")
        where = param.get("in")
        if where == "query":
            query_pairs.append((raw_name, rb_name))
        elif where == "header":
            header_pairs.append((raw_name, rb_name))
        elif where == "cookie":
            cookie_pairs.append((raw_name, rb_name))
        elif where == "path":
            path_name_map[raw_name] = rb_name

    body_type = _request_body_type(operation)
    body_required = bool((operation.get("requestBody") or {}).get("required"))
    if body_type is not None:
        if body_required:
            required_parts.append("body")
        else:
            optional_parts.append("body: nil")

    signature = ", ".join([*required_parts, *optional_parts])
    if signature:
        signature = f"({signature})"
    path_expr = _ruby_string(path)
    for raw_name in sorted(path_names, key=len, reverse=True):
        rb_name = path_name_map.get(raw_name, _identifier(raw_name))
        placeholder = _ruby_string("{" + raw_name + "}")
        path_expr += f".gsub({placeholder}, quote({rb_name}))"
    query_expr = (
        "{" + ", ".join(f"{_ruby_string(raw)} => {rb}" for raw, rb in query_pairs) + "}"
    )
    extra_args = ""
    if header_pairs:
        header_expr = ", ".join(
            f"{_ruby_string(raw)} => {rb}" for raw, rb in header_pairs
        )
        extra_args += f", headers: {{{header_expr}}}"
    if cookie_pairs:
        cookie_expr = ", ".join(
            f"{_ruby_string(raw)} => {rb}" for raw, rb in cookie_pairs
        )
        extra_args += f", cookies: {{{cookie_expr}}}"
    body_kwarg = {
        "json": "json_body",
        "form": "form_body",
        "multipart": "multipart_body",
    }[_request_body_kind(operation)]
    body_expr = "body" if body_type is not None else "nil"
    return (
        f"  def {name}{signature}\n"
        f"    path = {path_expr}\n"
        f"    request({method.upper()!r}, path, query: {query_expr}{extra_args}, "
        f"{body_kwarg}: {body_expr})\n"
        f"  end\n"
    )


def _generate_ruby(
    operations: list[tuple[str, str, dict[str, Any], str]], class_name: str
) -> str:
    methods = "\n".join(
        _ruby_method_source(method, path, operation, name)
        for method, path, operation, name in operations
    )
    if not methods:
        methods = "  # No operations were found in the OpenAPI schema.\n"
    return f"""require 'base64'
require 'json'
require 'net/http'
require 'uri'

class APIError < StandardError
  attr_reader :status_code, :body, :problem

  def initialize(status_code, body)
    has_problem = body.is_a?(Hash) && body.key?('status') && body.key?('title')
    @problem = has_problem ? body : nil
    message = if @problem
                @problem['title']
              else
                "API request failed with status #{{status_code}}"
              end
    super(message)
    @status_code = status_code
    @body = body
  end
end

class {class_name}
  def initialize(
    base_url = '', headers: {{}}, bearer_token: nil, basic_auth: nil, api_key: nil
  )
    @base_url = base_url.sub(%r{{/$}}, '')
    @headers = headers.dup
    @headers['Authorization'] = "Bearer #{{bearer_token}}" unless bearer_token.nil?
    unless basic_auth.nil?
      user, password = basic_auth
      encoded = Base64.strict_encode64("#{{user}}:#{{password}}")
      @headers['Authorization'] = 'Basic ' + encoded
    end
    unless api_key.nil?
      name, value = api_key
      @headers[name] = value
    end
  end

  def quote(value)
    URI.encode_www_form_component(value.to_s)
  end

  def request(
    method, path, query: {{}}, headers: {{}}, cookies: {{}},
    json_body: nil, form_body: nil, multipart_body: nil
  )
    query = query.reject {{ |_key, value| value.nil? }}
    uri = URI(@base_url + path)
    uri.query = URI.encode_www_form(query) unless query.empty?
    request_class = Net::HTTP.const_get(method.capitalize)
    req = request_class.new(uri)
    @headers.each {{ |key, value| req[key] = value }}
    headers.each {{ |key, value| req[key] = value.to_s unless value.nil? }}
    cookie_pairs = cookies.reject {{ |_key, value| value.nil? }}
    unless cookie_pairs.empty?
      cookie = cookie_pairs.map {{ |key, value| "#{{key}}=#{{value}}" }}.join('; ')
      cookie = "#{{req['Cookie']}}; #{{cookie}}" if req['Cookie']
      req['Cookie'] = cookie
    end
    if !multipart_body.nil?
      form = []
      multipart_body.each do |key, value|
        next if value.nil?
        if value.is_a?(Array)
          # [filename, content(, content_type)] — mirrors the Python client.
          opts = {{ filename: value[0].to_s }}
          opts[:content_type] = value[2].to_s if value.length > 2
          form << [key.to_s, value[1], opts]
        elsif value.respond_to?(:read)
          filename = key.to_s
          filename = File.basename(value.to_path) if value.respond_to?(:to_path)
          form << [key.to_s, value, {{ filename: filename }}]
        else
          form << [key.to_s, value.to_s]
        end
      end
      req.set_form(form, 'multipart/form-data')
    elsif !form_body.nil?
      req.set_form_data(form_body.reject {{ |_key, value| value.nil? }})
    elsif !json_body.nil?
      req['Content-Type'] ||= 'application/json'
      req.body = JSON.generate(json_body)
    end
    response = Net::HTTP.start(
      uri.hostname, uri.port, use_ssl: uri.scheme == 'https'
    ) do |http|
      http.request(req)
    end
    body = response.body.to_s.empty? ? nil : response.body
    content_type = response['content-type'].to_s
    body = JSON.parse(body) if body && content_type.include?('json')
    raise APIError.new(response.code.to_i, body) if response.code.to_i >= 400
    body
  end

{methods}end
"""


def _php_method_source(
    method: str, path: str, operation: dict[str, Any], name: str
) -> str:
    path_names = _path_params(path)
    parameters = operation.get("parameters") or []
    required_parts = []
    optional_parts = []
    query_pairs = []
    header_pairs = []
    cookie_pairs = []
    path_name_map: dict[str, str] = {}
    # Names the method body relies on must never be shadowed by a parameter.
    seen_names = {"body", "path"}

    for param in parameters:
        if not isinstance(param, dict):
            continue
        raw_name = str(param.get("name", "value"))
        php_name = _php_identifier(raw_name)
        while php_name in seen_names:
            php_name += "_"
        seen_names.add(php_name)
        default = _param_default(param)
        if default is None:
            required_parts.append(f"${php_name}")
        else:
            optional_parts.append(f"${php_name} = null")
        where = param.get("in")
        if where == "query":
            query_pairs.append((raw_name, php_name))
        elif where == "header":
            header_pairs.append((raw_name, php_name))
        elif where == "cookie":
            cookie_pairs.append((raw_name, php_name))
        elif where == "path":
            path_name_map[raw_name] = php_name

    body_type = _request_body_type(operation)
    body_required = bool((operation.get("requestBody") or {}).get("required"))
    if body_type is not None:
        if body_required:
            required_parts.append("$body")
        else:
            optional_parts.append("$body = null")

    signature = ", ".join([*required_parts, *optional_parts])
    path_lines = [f"        $path = {_php_string(path)};"]
    for raw_name in sorted(path_names, key=len, reverse=True):
        php_name = path_name_map.get(raw_name, _php_identifier(raw_name))
        path_lines.append(
            f"        $path = str_replace("
            f"{_php_string('{' + raw_name + '}')}, "
            f"$this->quote(${php_name}), $path);"
        )
    query_expr = (
        "["
        + ", ".join(
            f"{_php_string(raw)} => ${php_name}" for raw, php_name in query_pairs
        )
        + "]"
    )
    extra_args = ""
    if header_pairs:
        header_expr = ", ".join(
            f"{_php_string(raw)} => ${php_name}" for raw, php_name in header_pairs
        )
        extra_args += f", headers: [{header_expr}]"
    if cookie_pairs:
        cookie_expr = ", ".join(
            f"{_php_string(raw)} => ${php_name}" for raw, php_name in cookie_pairs
        )
        extra_args += f", cookies: [{cookie_expr}]"
    body_kwarg = {
        "json": "jsonBody",
        "form": "formBody",
        "multipart": "multipartBody",
    }[_request_body_kind(operation)]
    body_expr = "$body" if body_type is not None else "null"
    path_src = "\n".join(path_lines)
    method_literal = _php_string(method.upper())
    return (
        f"    public function {name}({signature}): mixed\n"
        f"    {{\n"
        f"{path_src}\n"
        f"        return $this->request({method_literal}, $path, {query_expr}"
        f"{extra_args}, {body_kwarg}: {body_expr});\n"
        f"    }}\n"
    )


def _generate_php(
    operations: list[tuple[str, str, dict[str, Any], str]], class_name: str
) -> str:
    methods = "\n".join(
        _php_method_source(method, path, operation, name)
        for method, path, operation, name in operations
    )
    if not methods:
        methods = "    // No operations were found in the OpenAPI schema.\n"
    return f"""<?php

class APIError extends Exception
{{
    public int $statusCode;
    public mixed $body;
    public ?array $problem;

    public function __construct(int $statusCode, mixed $body)
    {{
        $this->problem = is_array($body) && isset($body['status'], $body['title'])
            ? $body
            : null;
        parent::__construct(
            $this->problem['title'] ?? ("API request failed with status " . $statusCode)
        );
        $this->statusCode = $statusCode;
        $this->body = $body;
    }}
}}

class {class_name}
{{
    private string $baseUrl;
    private array $headers;

    public function __construct(
        string $baseUrl = '',
        array $headers = [],
        ?string $bearerToken = null,
        ?array $basicAuth = null,
        ?array $apiKey = null
    ) {{
        $this->baseUrl = rtrim($baseUrl, '/');
        $this->headers = $headers;
        if ($bearerToken !== null) {{
            $this->headers['Authorization'] = 'Bearer ' . $bearerToken;
        }}
        if ($basicAuth !== null) {{
            [$user, $password] = $basicAuth;
            $encoded = base64_encode($user . ':' . $password);
            $this->headers['Authorization'] = 'Basic ' . $encoded;
        }}
        if ($apiKey !== null) {{
            [$name, $value] = $apiKey;
            $this->headers[$name] = $value;
        }}
    }}

    private function quote(mixed $value): string
    {{
        return rawurlencode((string) $value);
    }}

    private function request(
        string $method,
        string $path,
        array $query = [],
        mixed $jsonBody = null,
        array $headers = [],
        array $cookies = [],
        mixed $formBody = null,
        mixed $multipartBody = null
    ): mixed
    {{
        $query = array_filter($query, fn($value) => $value !== null);
        $url = $this->baseUrl . $path;
        if ($query) $url .= '?' . http_build_query($query);
        $headers = array_merge(
            $this->headers,
            array_filter($headers, fn($value) => $value !== null)
        );
        $cookies = array_filter($cookies, fn($value) => $value !== null);
        if ($cookies) {{
            $pairs = [];
            foreach ($cookies as $key => $value) $pairs[] = $key . '=' . $value;
            $cookie = implode('; ', $pairs);
            if (isset($headers['Cookie'])) {{
                $cookie = $headers['Cookie'] . '; ' . $cookie;
            }}
            $headers['Cookie'] = $cookie;
        }}
        $content = null;
        if ($multipartBody !== null) {{
            $boundary = bin2hex(random_bytes(16));
            $content = '';
            // A file spec is exactly ['content' => ..., 'filename' => ?,
            // 'content_type' => ?]; any other array is a repeated form field.
            $isFileSpec = fn($value) => is_array($value)
                && array_key_exists('content', $value)
                && !array_diff(
                    array_keys($value),
                    ['filename', 'content', 'content_type']
                );
            foreach ($multipartBody as $key => $value) {{
                if ($value === null) continue;
                $filename = null;
                $partType = null;
                $items = [$value];
                if ($isFileSpec($value)) {{
                    $filename = $value['filename'] ?? $key;
                    $partType = $value['content_type'] ?? 'application/octet-stream';
                    $items = [$value['content']];
                }} elseif (is_array($value)) {{
                    $items = $value;
                }}
                foreach ($items as $data) {{
                    if ($data === null) continue;
                    $content .= '--' . $boundary . "\\r\\n";
                    $disposition = 'Content-Disposition: form-data; name="'
                        . $key . '"';
                    if ($filename !== null) {{
                        $disposition .= '; filename="' . $filename . '"';
                    }}
                    $content .= $disposition . "\\r\\n";
                    if ($partType !== null) {{
                        $content .= 'Content-Type: ' . $partType . "\\r\\n";
                    }}
                    $content .= "\\r\\n" . $data . "\\r\\n";
                }}
            }}
            $content .= '--' . $boundary . "--\\r\\n";
            $headers['Content-Type'] = 'multipart/form-data; boundary=' . $boundary;
        }} elseif ($formBody !== null) {{
            $content = http_build_query(
                array_filter($formBody, fn($value) => $value !== null)
            );
            $headers['Content-Type'] = $headers['Content-Type']
                ?? 'application/x-www-form-urlencoded';
        }} elseif ($jsonBody !== null) {{
            $content = json_encode($jsonBody);
            $headers['Content-Type'] = $headers['Content-Type'] ?? 'application/json';
        }}
        $headerLines = [];
        foreach ($headers as $key => $value) $headerLines[] = $key . ': ' . $value;
        $context = stream_context_create(['http' => [
            'method' => $method,
            'header' => implode("\\r\\n", $headerLines),
            'content' => $content,
            'ignore_errors' => true,
        ]]);
        $body = file_get_contents($url, false, $context);
        $status = 0;
        foreach ($http_response_header ?? [] as $header) {{
            if (preg_match('/^HTTP\\/\\S+\\s+(\\d+)/', $header, $matches)) {{
                $status = (int) $matches[1];
                break;
            }}
        }}
        $contentType = '';
        foreach ($http_response_header ?? [] as $header) {{
            if (stripos($header, 'Content-Type:') === 0) $contentType = $header;
        }}
        $payload = $body === false || $body === '' ? null : $body;
        if ($payload !== null && stripos($contentType, 'json') !== false) {{
            $payload = json_decode($payload, true);
        }}
        if ($status >= 400) throw new APIError($status, $payload);
        return $payload;
    }}

{methods}}}
"""


def generate_client(
    source: Any, *, class_name: str = "APIClient", language: str = "python"
) -> str:
    """Return source for a client generated from ``source``.

    ``source`` can be a Responder ``API`` with OpenAPI enabled, an
    ``OpenAPISchema`` instance, an OpenAPI dict, or a YAML string.
    """
    language = language.lower()
    if language not in _LANGUAGES:
        raise ValueError(
            "language must be one of: " + ", ".join(sorted(_LANGUAGES))
        )
    spec = _load_spec(source)
    class_name = _class_name(class_name)
    type_names = _component_type_names(spec)
    py_schemas, js_schemas = _component_schema_literals(spec)
    operations = _operations(spec)
    if language == "javascript":
        return _generate_javascript(
            operations, class_name, component_schemas=js_schemas
        )
    if language == "typescript":
        return _generate_javascript(
            operations,
            class_name,
            typed=True,
            type_names=type_names,
            type_defs=_ts_type_defs(spec, type_names),
            component_schemas=js_schemas,
        )
    if language == "ruby":
        return _generate_ruby(operations, class_name)
    if language == "php":
        return _generate_php(operations, class_name)

    methods = [
        _method_source(method, path, operation, name, type_names)
        for method, path, operation, name in operations
    ]

    if not methods:
        methods.append("    pass\n")

    methods_src = "\n".join(methods)
    type_defs = _python_type_defs(spec, type_names)
    return f'''from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from typing import Any, TypedDict


def _quote(value: Any) -> str:
    return urllib.parse.quote(str(value), safe="")


class APIError(Exception):
    def __init__(self, status_code: int, body: Any):
        problem = _problem_details(body)
        message = problem.get("title") if problem else None
        super().__init__(message or f"API request failed with status {{status_code}}")
        self.status_code = status_code
        self.body = body
        self.problem = problem
        self.title = problem.get("title") if problem else None
        self.detail = problem.get("detail") if problem else None
        self.errors = problem.get("errors") if problem else None


class APIValidationError(Exception):
    def __init__(self, path: str, expected: str, value: Any):
        super().__init__(f"{{path}} expected {{expected}}")
        self.path = path
        self.expected = expected
        self.value = value


_SCHEMAS: dict[str, Any] = {py_schemas}


def _resolve_schema(schema: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not schema or "$ref" not in schema:
        return schema
    prefix = "#/components/schemas/"
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith(prefix):
        return schema
    return _SCHEMAS.get(ref.removeprefix(prefix), schema)


def _validate_value(
    value: Any, schema: Mapping[str, Any] | None, path: str = "value"
) -> None:
    schema = _resolve_schema(schema)
    if not schema:
        return
    if schema.get("nullable") and value is None:
        return
    variants = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(variants, list):
        for variant in variants:
            try:
                _validate_value(value, variant, path)
                return
            except APIValidationError:
                continue
        raise APIValidationError(path, "one allowed schema", value)
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for variant in all_of:
            _validate_value(value, variant, path)
        return
    typ = schema.get("type")
    if typ == "null":
        if value is not None:
            raise APIValidationError(path, "null", value)
        return
    if typ == "integer":
        if type(value) is not int:
            raise APIValidationError(path, "integer", value)
        return
    if typ == "number":
        if type(value) not in (int, float):
            raise APIValidationError(path, "number", value)
        return
    if typ == "boolean":
        if type(value) is not bool:
            raise APIValidationError(path, "boolean", value)
        return
    if typ == "string":
        if not isinstance(value, str):
            raise APIValidationError(path, "string", value)
        return
    if typ == "array":
        if not isinstance(value, list):
            raise APIValidationError(path, "array", value)
        for index, item in enumerate(value):
            _validate_value(item, schema.get("items") or {{}}, f"{{path}}[{{index}}]")
        return
    if typ == "object":
        if not isinstance(value, Mapping):
            raise APIValidationError(path, "object", value)
        for key in schema.get("required") or []:
            if key not in value:
                raise APIValidationError(f"{{path}}.{{key}}", "present", None)
        for key, prop_schema in (schema.get("properties") or {{}}).items():
            if key in value:
                _validate_value(value[key], prop_schema, f"{{path}}.{{key}}")


class APIProblem(TypedDict, total=False):
    type: str
    title: str
    status: int
    detail: str
    instance: str
    request_id: str
    errors: list[dict[str, Any]]


def _problem_details(payload: Any) -> APIProblem | None:
    if not isinstance(payload, Mapping):
        return None
    if not isinstance(payload.get("status"), int):
        return None
    if not isinstance(payload.get("title"), str):
        return None
    return dict(payload)


{type_defs}\
class {class_name}:
    def __init__(
        self,
        base_url: str = "",
        *,
        headers: Mapping[str, str] | None = None,
        bearer_token: str | None = None,
        basic_auth: tuple[str, str] | None = None,
        api_key: tuple[str, str] | None = None,
        session: Any | None = None,
        timeout: float | None = None,
        validate: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.headers = dict(headers or {{}})
        self.session = session
        self.timeout = timeout
        self.validate = validate
        if bearer_token is not None:
            self.headers["Authorization"] = f"Bearer {{bearer_token}}"
        if basic_auth is not None:
            user, password = basic_auth
            raw = f"{{user}}:{{password}}".encode("utf-8")
            self.headers["Authorization"] = (
                "Basic " + base64.b64encode(raw).decode("ascii")
            )
        if api_key is not None:
            name, value = api_key
            self.headers[name] = value

    def _decode_response(
        self,
        status_code: int,
        headers: Any,
        body: bytes,
        response_schema: Mapping[str, Any] | None = None,
    ) -> Any:
        content_type = headers.get("content-type", headers.get("Content-Type", ""))
        if body and "json" in content_type:
            payload = json.loads(body.decode("utf-8"))
        elif body:
            try:
                payload = body.decode("utf-8")
            except UnicodeDecodeError:
                payload = body
        else:
            payload = None
        if status_code >= 400:
            raise APIError(status_code, payload)
        if self.validate and response_schema is not None:
            _validate_value(payload, response_schema, "response")
        return payload

    def _merged_headers(
        self,
        headers: Mapping[str, Any] | None,
        cookies: Mapping[str, Any] | None,
    ) -> dict[str, str]:
        merged = dict(self.headers)
        for key, value in (headers or {{}}).items():
            if value is not None:
                merged[str(key)] = str(value)
        pairs = [
            str(key) + "=" + str(value)
            for key, value in (cookies or {{}}).items()
            if value is not None
        ]
        if pairs:
            cookie = "; ".join(pairs)
            if merged.get("Cookie"):
                cookie = merged["Cookie"] + "; " + cookie
            merged["Cookie"] = cookie
        return merged

    @staticmethod
    def _split_multipart(
        body: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Split a multipart body into (fields, files) for httpx-style sessions."""
        fields: dict[str, Any] = {{}}
        files: dict[str, Any] = {{}}
        for key, value in body.items():
            if value is None:
                continue
            if isinstance(value, (bytes, bytearray, tuple)) or hasattr(value, "read"):
                files[key] = value
            else:
                fields[key] = value
        return fields, files

    @staticmethod
    def _encode_multipart(body: Mapping[str, Any]) -> tuple[bytes, str]:
        """Encode a multipart/form-data payload; returns (data, content_type).

        File values may be bytes, a file-like object, or a
        ``(filename, content[, content_type])`` tuple; other values are fields.
        """
        boundary = uuid.uuid4().hex
        chunks: list[bytes] = []
        for key, value in body.items():
            if value is None:
                continue
            filename = None
            part_type = None
            content: Any = value
            if isinstance(value, tuple):
                filename, content = str(value[0]), value[1]
                if len(value) > 2:
                    part_type = str(value[2])
            elif hasattr(value, "read"):
                filename = str(getattr(value, "name", key))
                content = value.read()
            elif isinstance(value, (bytes, bytearray)):
                filename = str(key)
            head = "--" + boundary + "\\r\\n"
            head += 'Content-Disposition: form-data; name="' + str(key) + '"'
            if filename is not None:
                head += '; filename="' + filename + '"'
            head += "\\r\\n"
            if filename is not None:
                part_type = part_type or "application/octet-stream"
                head += "Content-Type: " + part_type + "\\r\\n"
            head += "\\r\\n"
            if isinstance(content, str):
                payload = content.encode("utf-8")
            elif isinstance(content, (bytes, bytearray)):
                payload = bytes(content)
            else:
                payload = str(content).encode("utf-8")
            chunks.append(head.encode("utf-8") + payload + b"\\r\\n")
        chunks.append(("--" + boundary + "--\\r\\n").encode("utf-8"))
        return b"".join(chunks), "multipart/form-data; boundary=" + boundary

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        headers: Mapping[str, Any] | None = None,
        cookies: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        form_body: Mapping[str, Any] | None = None,
        multipart_body: Mapping[str, Any] | None = None,
        request_schema: Mapping[str, Any] | None = None,
        response_schema: Mapping[str, Any] | None = None,
    ) -> Any:
        query = {{k: v for k, v in (query or {{}}).items() if v is not None}}
        headers = self._merged_headers(headers, cookies)
        body_payload = json_body if json_body is not None else form_body
        if self.validate and body_payload is not None and request_schema is not None:
            _validate_value(body_payload, request_schema, "body")
        if self.session is not None:
            data = None
            files = None
            if multipart_body is not None:
                data, files = self._split_multipart(multipart_body)
            elif form_body is not None:
                data = {{k: v for k, v in form_body.items() if v is not None}}
            response = self.session.request(
                method,
                path,
                params=query or None,
                json=json_body,
                data=data or None,
                files=files or None,
                headers=headers,
            )
            return self._decode_response(
                response.status_code,
                response.headers,
                response.content,
                response_schema,
            )

        if not self.base_url:
            raise ValueError("base_url is required when no session is provided")
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        data = None
        if multipart_body is not None:
            data, content_type = self._encode_multipart(multipart_body)
            headers["Content-Type"] = content_type
        elif form_body is not None:
            form = {{k: v for k, v in form_body.items() if v is not None}}
            data = urllib.parse.urlencode(form, doseq=True).encode("utf-8")
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return self._decode_response(
                    response.status,
                    response.headers,
                    response.read(),
                    response_schema,
                )
        except urllib.error.HTTPError as exc:
            return self._decode_response(
                exc.code, exc.headers, exc.read(), response_schema
            )

{methods_src}
'''


def write_client(
    source: Any,
    path: str | Path,
    *,
    class_name: str = "APIClient",
    language: str = "python",
) -> Path:
    """Generate a client and write it to ``path``."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        generate_client(source, class_name=class_name, language=language)
    )
    return target
