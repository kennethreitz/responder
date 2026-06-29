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


def _load_spec(source) -> dict[str, Any]:
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


def _schema_type(schema: dict[str, Any] | None) -> str:
    """Translate a small OpenAPI schema fragment to a Python type string."""
    if not schema:
        return "Any"
    if "$ref" in schema:
        return "dict[str, Any]"
    if "anyOf" in schema or "oneOf" in schema:
        variants = schema.get("anyOf") or schema.get("oneOf") or []
        non_null = [v for v in variants if v != {"type": "null"}]
        if len(non_null) == 1 and len(non_null) != len(variants):
            return f"{_schema_type(non_null[0])} | None"
        return "Any"
    typ = schema.get("type")
    if typ == "integer":
        return "int"
    if typ == "number":
        return "float"
    if typ == "boolean":
        return "bool"
    if typ == "array":
        return f"list[{_schema_type(schema.get('items'))}]"
    if typ == "object":
        return "dict[str, Any]"
    return "str"


def _ts_schema_type(schema: dict[str, Any] | None) -> str:
    """Translate a small OpenAPI schema fragment to TypeScript."""
    if not schema:
        return "unknown"
    if "$ref" in schema:
        return "Record<string, unknown>"
    if "anyOf" in schema or "oneOf" in schema:
        variants = schema.get("anyOf") or schema.get("oneOf") or []
        non_null = [v for v in variants if v != {"type": "null"}]
        if len(non_null) == 1 and len(non_null) != len(variants):
            return f"{_ts_schema_type(non_null[0])} | null"
        return "unknown"
    typ = schema.get("type")
    if typ in ("integer", "number"):
        return "number"
    if typ == "boolean":
        return "boolean"
    if typ == "array":
        return f"Array<{_ts_schema_type(schema.get('items'))}>"
    if typ == "object":
        return "Record<string, unknown>"
    return "string"


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


def _request_body_type(operation: dict[str, Any]) -> str | None:
    body = operation.get("requestBody")
    if not isinstance(body, dict):
        return None
    content = body.get("content") or {}
    media: Any
    media = content.get("application/json") or next(iter(content.values()), {})
    schema = media.get("schema") if isinstance(media, dict) else None
    if isinstance(schema, dict):
        return _schema_type(schema)
    return "Mapping[str, Any]"


def _ts_request_body_type(operation: dict[str, Any]) -> str | None:
    body = operation.get("requestBody")
    if not isinstance(body, dict):
        return None
    content = body.get("content") or {}
    media: Any
    media = content.get("application/json") or next(iter(content.values()), {})
    schema = media.get("schema") if isinstance(media, dict) else None
    if isinstance(schema, dict):
        return _ts_schema_type(schema)
    return "Record<string, unknown>"


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


def _method_source(
    method: str,
    path: str,
    operation: dict[str, Any],
    name: str,
) -> str:
    path_names = _path_params(path)
    parameters = operation.get("parameters") or []
    required_parts = []
    optional_parts = []
    query_pairs = []
    seen_python_names = set()

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
        annotation = _schema_type(schema)
        if default == "None" and " | None" not in annotation:
            annotation = f"{annotation} | None"
        part = f"{py_name}: {annotation}"
        if default is not None:
            part += f" = {default}"
            optional_parts.append(part)
        else:
            required_parts.append(part)

        if param.get("in") == "query":
            query_pairs.append((raw_name, py_name))

    body_type = _request_body_type(operation)
    body_required = bool((operation.get("requestBody") or {}).get("required"))
    if body_type is not None:
        if body_required:
            required_parts.append(f"body: {body_type}")
        else:
            optional_parts.append(f"body: {body_type} | None = None")

    signature = ", ".join(["self", *required_parts, *optional_parts])
    path_expr = repr(path)
    for raw_name in sorted(path_names, key=len, reverse=True):
        py_name = _identifier(raw_name)
        path_expr += f".replace('{{{raw_name}}}', _quote({py_name}))"

    query_expr = "{" + ", ".join(f"{raw!r}: {py}" for raw, py in query_pairs) + "}"
    body_expr = "body" if body_type is not None else "None"
    return (
        f"    def {name}({signature}) -> Any:\n"
        f"        path = {path_expr}\n"
        f"        return self._request({method.upper()!r}, path, "
        f"query={query_expr}, json_body={body_expr})\n"
    )


def _js_string(value: str) -> str:
    return repr(value)


def _php_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _js_method_source(
    method: str,
    path: str,
    operation: dict[str, Any],
    name: str,
    *,
    typed: bool = False,
) -> str:
    path_names = _path_params(path)
    parameters = operation.get("parameters") or []
    required_parts = []
    optional_parts = []
    query_pairs = []
    seen_names = set()

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
            annotation = _ts_schema_type(schema)
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
        if param.get("in") == "query":
            query_pairs.append((raw_name, js_name))

    if typed:
        body_type = _ts_request_body_type(operation)
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
        js_name = _camel_identifier(raw_name)
        path_expr += f".replace('{{{raw_name}}}', quote({js_name}))"
    query_expr = (
        "{"
        + ", ".join(f"{_js_string(raw)}: {js_name}" for raw, js_name in query_pairs)
        + "}"
    )
    body_expr = "body" if body_type is not None else "null"
    return_type = ": Promise<unknown>" if typed else ""
    return (
        f"  {name}({signature}){return_type} {{\n"
        f"    const path = {path_expr};\n"
        f"    return this.request({_js_string(method.upper())}, path, "
        f"{{ query: {query_expr}, body: {body_expr} }});\n"
        f"  }}\n"
    )


def _generate_javascript(
    operations: list[tuple[str, str, dict[str, Any], str]],
    class_name: str,
    *,
    typed: bool = False,
) -> str:
    methods = "\n".join(
        _js_method_source(method, path, operation, name, typed=typed)
        for method, path, operation, name in operations
    )
    if not methods:
        methods = "  // No operations were found in the OpenAPI schema.\n"
    export = "export "
    type_bits = """
type HeadersMap = Record<string, string>;
type RequestOptions = { query?: Record<string, unknown>; body?: unknown };
type FetchFunction = typeof fetch;

""" if typed else ""
    api_error_field_bits = """  statusCode: number;
  body: unknown;

""" if typed else ""
    field_bits = """  baseUrl: string;
  headers: HeadersMap;
  fetchImpl: FetchFunction;

""" if typed else ""
    ctor_sig = (
        "baseUrl = '', { headers = {}, bearerToken = null, basicAuth = null, "
        "apiKey = null, fetchImpl = fetch } = {}"
    )
    if typed:
        ctor_sig = (
            "baseUrl: string = '', { headers = {}, bearerToken = null, "
            "basicAuth = null, apiKey = null, fetchImpl = fetch }: { "
            "headers?: HeadersMap; bearerToken?: string | null; "
            "basicAuth?: [string, string] | null; apiKey?: [string, string] | null; "
            "fetchImpl?: FetchFunction } = {}"
        )
    request_sig = "async request(method, path, { query = {}, body = null } = {})"
    if typed:
        request_sig = (
            "async request(method: string, path: string, "
            "{ query = {}, body = null }: RequestOptions = {}): Promise<unknown>"
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
    quote_decl = (
        f"const quote = (value{quote_type}) => "
        "encodeURIComponent(String(value));"
    )
    return f"""{type_bits}{quote_decl}

const encodeBase64 = {encode_sig} => {{
  if (typeof btoa === 'function') return btoa(value);
  const bufferCtor = {buffer_ctor};
  if (bufferCtor) return bufferCtor.from(value, 'utf8').toString('base64');
  throw new Error('No base64 encoder is available');
}};

export class APIError extends Error {{
{api_error_field_bits}  constructor({status_arg}, {body_arg}) {{
    super(`API request failed with status ${{statusCode}}`);
    this.statusCode = statusCode;
    this.body = body;
  }}
}}

{export}class {class_name} {{
{field_bits}  constructor({ctor_sig}) {{
    this.baseUrl = baseUrl.replace(/\\/$/, '');
    this.headers = {{ ...headers }};
    this.fetchImpl = fetchImpl;
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
    const headers = {{ ...this.headers }};
    const init{init_type} = {{ method, headers }};
    if (body !== null && body !== undefined) {{
      headers['Content-Type'] = headers['Content-Type'] || 'application/json';
      init.body = JSON.stringify(body);
    }}
    const response = await this.fetchImpl(url, init);
    const contentType = response.headers.get('content-type') || '';
    const text = await response.text();
    const payload = text && contentType.includes('json')
      ? JSON.parse(text)
      : text || null;
    if (!response.ok) throw new APIError(response.status, payload);
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

    for param in parameters:
        if not isinstance(param, dict):
            continue
        raw_name = str(param.get("name", "value"))
        rb_name = _identifier(raw_name)
        default = _param_default(param)
        if default is None:
            required_parts.append(rb_name)
        else:
            optional_parts.append(f"{rb_name}: nil")
        if param.get("in") == "query":
            query_pairs.append((raw_name, rb_name))

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
    path_expr = repr(path)
    for raw_name in sorted(path_names, key=len, reverse=True):
        rb_name = _identifier(raw_name)
        path_expr += f".gsub('{{{raw_name}}}', quote({rb_name}))"
    query_expr = "{" + ", ".join(f"{raw!r} => {rb}" for raw, rb in query_pairs) + "}"
    body_expr = "body" if body_type is not None else "nil"
    return (
        f"  def {name}{signature}\n"
        f"    path = {path_expr}\n"
        f"    request({method.upper()!r}, path, query: {query_expr}, "
        f"json_body: {body_expr})\n"
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
  attr_reader :status_code, :body

  def initialize(status_code, body)
    super("API request failed with status #{{status_code}}")
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

  def request(method, path, query: {{}}, json_body: nil)
    query = query.reject {{ |_key, value| value.nil? }}
    uri = URI(@base_url + path)
    uri.query = URI.encode_www_form(query) unless query.empty?
    request_class = Net::HTTP.const_get(method.capitalize)
    req = request_class.new(uri)
    @headers.each {{ |key, value| req[key] = value }}
    unless json_body.nil?
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

    for param in parameters:
        if not isinstance(param, dict):
            continue
        raw_name = str(param.get("name", "value"))
        php_name = _php_identifier(raw_name)
        default = _param_default(param)
        if default is None:
            required_parts.append(f"${php_name}")
        else:
            optional_parts.append(f"${php_name} = null")
        if param.get("in") == "query":
            query_pairs.append((raw_name, php_name))

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
        php_name = _php_identifier(raw_name)
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
    body_expr = "$body" if body_type is not None else "null"
    path_src = "\n".join(path_lines)
    method_literal = _php_string(method.upper())
    return (
        f"    public function {name}({signature}): mixed\n"
        f"    {{\n"
        f"{path_src}\n"
        f"        return $this->request({method_literal}, $path, {query_expr}, "
        f"{body_expr});\n"
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

    public function __construct(int $statusCode, mixed $body)
    {{
        parent::__construct("API request failed with status " . $statusCode);
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
        mixed $jsonBody = null
    ): mixed
    {{
        $query = array_filter($query, fn($value) => $value !== null);
        $url = $this->baseUrl . $path;
        if ($query) $url .= '?' . http_build_query($query);
        $headers = $this->headers;
        $content = null;
        if ($jsonBody !== null) {{
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
    source, *, class_name: str = "APIClient", language: str = "python"
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
    operations = _operations(spec)
    if language == "javascript":
        return _generate_javascript(operations, class_name)
    if language == "typescript":
        return _generate_javascript(operations, class_name, typed=True)
    if language == "ruby":
        return _generate_ruby(operations, class_name)
    if language == "php":
        return _generate_php(operations, class_name)

    methods = [
        _method_source(method, path, operation, name)
        for method, path, operation, name in operations
    ]

    if not methods:
        methods.append("    pass\n")

    methods_src = "\n".join(methods)
    return f'''from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any


def _quote(value: Any) -> str:
    return urllib.parse.quote(str(value), safe="")


class APIError(Exception):
    def __init__(self, status_code: int, body: Any):
        super().__init__(f"API request failed with status {{status_code}}")
        self.status_code = status_code
        self.body = body


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
    ):
        self.base_url = base_url.rstrip("/")
        self.headers = dict(headers or {{}})
        self.session = session
        self.timeout = timeout
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

    def _decode_response(self, status_code: int, headers: Any, body: bytes) -> Any:
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
        return payload

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
    ) -> Any:
        query = {{k: v for k, v in (query or {{}}).items() if v is not None}}
        if self.session is not None:
            response = self.session.request(
                method,
                path,
                params=query or None,
                json=json_body,
                headers=self.headers,
            )
            return self._decode_response(
                response.status_code, response.headers, response.content
            )

        if not self.base_url:
            raise ValueError("base_url is required when no session is provided")
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        headers = dict(self.headers)
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return self._decode_response(
                    response.status, response.headers, response.read()
                )
        except urllib.error.HTTPError as exc:
            return self._decode_response(exc.code, exc.headers, exc.read())

{methods_src}
'''


def write_client(
    source, path, *, class_name: str = "APIClient", language: str = "python"
) -> Path:
    """Generate a client and write it to ``path``."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        generate_client(source, class_name=class_name, language=language)
    )
    return target
