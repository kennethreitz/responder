import logging
import re
from pathlib import Path
from typing import Any

from apispec import APISpec, yaml_utils
from apispec.ext.marshmallow import MarshmallowPlugin

from responder import status_codes
from responder.contracts import (
    inferred_response_model,
    response_media_type,
    response_status_allows_body,
)
from responder.errors import status_title
from responder.statics import API_THEMES, DEFAULT_OPENAPI_THEME
from responder.templates import Templates

logger = logging.getLogger("responder.openapi")

# JSON Schema fragments for route path convertors.
_CONVERTOR_SCHEMAS = {
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "str": {"type": "string"},
    "path": {"type": "string"},
    "uuid": {"type": "string", "format": "uuid"},
}

_COMMON_PROBLEM_STATUSES = {
    "400": "Bad Request",
    "404": "Not Found",
    "405": "Method Not Allowed",
    "500": "Internal Server Error",
}
_AUTH_INJECTION_NAMES = frozenset({"auth", "principal", "user"})
_CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _problem_details_schema() -> dict:
    return {
        "type": "object",
        "required": ["type", "title", "status"],
        "properties": {
            "type": {"type": "string", "format": "uri-reference"},
            "title": {"type": "string"},
            "status": {"type": "integer"},
            "detail": {"type": "string"},
            "instance": {"type": "string", "format": "uri-reference"},
            "request_id": {"type": "string"},
            "errors": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": True},
            },
        },
        "additionalProperties": True,
    }


def _problem_response(description: str, *, validation: bool = False) -> dict:
    return {
        "description": description,
        "content": {
            "application/problem+json": {
                "schema": {"$ref": "#/components/schemas/ProblemDetails"}
            }
        },
    }


def _legacy_error_response(description: str, *, validation: bool = False) -> dict:
    if validation:
        schema = {
            "type": "object",
            "properties": {
                "errors": {
                    "type": "array",
                    "items": {"type": "object", "additionalProperties": True},
                }
            },
        }
    else:
        schema = {
            "type": "object",
            "properties": {"error": {"type": "string"}},
        }
    return {
        "description": description,
        "content": {"application/json": {"schema": schema}},
    }


_COMPONENT_REF_PREFIX = "#/components/schemas/"


def _rename_refs_inplace(obj: Any, renames: dict[str, str]) -> None:
    """Rewrite ``#/components/schemas/X`` refs in place per ``renames``."""
    if isinstance(obj, dict):
        ref = obj.get("$ref")
        if isinstance(ref, str) and ref.startswith(_COMPONENT_REF_PREFIX):
            name = ref.removeprefix(_COMPONENT_REF_PREFIX)
            if name in renames:
                obj["$ref"] = _COMPONENT_REF_PREFIX + renames[name]
        for value in obj.values():
            _rename_refs_inplace(value, renames)
    elif isinstance(obj, list):
        for item in obj:
            _rename_refs_inplace(item, renames)


def _hoist_defs(schema: dict, defs: dict | None) -> dict:
    """Move a schema's ``$defs`` into ``defs`` so they can be registered as
    components (the refs already point at ``#/components/schemas/``).

    Two distinct schemas sharing a bare name (e.g. same-named enums from
    different modules) must not silently merge into one component: on a
    collision with a different body, the incoming definition gets a stable
    numeric suffix and the schema's refs are rewritten to match. Identical
    bodies share a single component.
    """
    hoisted = schema.pop("$defs", None)
    if not hoisted:
        return schema
    if defs is None:
        schema["$defs"] = hoisted  # no collector: keep them resolvable inline
        return schema
    renames: dict[str, str] = {}
    for name, body in hoisted.items():
        if name not in defs or defs[name] == body:
            continue
        index = 2
        new_name = f"{name}_{index}"
        while (new_name in defs and defs[new_name] != body) or new_name in hoisted:
            index += 1
            new_name = f"{name}_{index}"
        renames[name] = new_name
    if renames:
        _rename_refs_inplace(schema, renames)
        _rename_refs_inplace(hoisted, renames)
        hoisted = {renames.get(name, name): body for name, body in hoisted.items()}
    defs.update(hoisted)
    return schema


def _json_schema_from_adapter(adapter: Any, defs: dict | None = None) -> dict:
    """Best-effort JSON Schema from a Pydantic adapter.

    Nested models/enums are referenced as ``#/components/schemas/...`` and
    their definitions are collected into ``defs`` for component registration.
    """
    if adapter is None:
        return {"type": "string"}
    try:
        schema = adapter.json_schema(ref_template="#/components/schemas/{model}")
        _hoist_defs(schema, defs)
        schema.pop("title", None)
        return schema
    except Exception:
        return {"type": "string"}


def _json_schema_from_annotation(
    annotation: Any, defs: dict | None = None
) -> dict | None:
    """Best-effort JSON Schema for a plain annotation."""
    try:
        from pydantic import TypeAdapter
    except ImportError:  # pragma: no cover - pydantic is a core dep
        return None
    try:
        schema = TypeAdapter(annotation).json_schema(
            ref_template="#/components/schemas/{model}"
        )
        _hoist_defs(schema, defs)
        schema.pop("title", None)
        return schema
    except Exception:
        return None


def _path_parameters(route: Any, endpoint: Any, defs: dict | None = None) -> list[dict]:
    """OpenAPI ``parameters`` entries for a route's path parameters."""
    convertor_names = getattr(route, "param_convertor_names", {}) or {}
    parameters = {
        name: {
            "name": name,
            "in": "path",
            "required": True,
            "schema": dict(_CONVERTOR_SCHEMAS.get(convertor, {"type": "string"})),
        }
        for name, convertor in convertor_names.items()
    }
    if not parameters:
        return []

    specs = _marker_specs(endpoint)
    explicit_lookups = set()
    explicit_names = set()
    for spec in specs:
        if spec.location != "path" or spec.lookup not in parameters:
            continue
        explicit_lookups.add(spec.lookup)
        explicit_names.add(spec.name)
        parameter = parameters[spec.lookup]
        parameter["schema"] = _json_schema_from_adapter(spec.adapter, defs)
        if spec.marker.description:
            parameter["description"] = spec.marker.description
        if spec.marker.deprecated:
            parameter["deprecated"] = True

    hints = _handler_hints(endpoint)
    for name, convertor in convertor_names.items():
        if name in explicit_lookups or name in explicit_names or name not in hints:
            continue
        # Default/plain string segments can inherit a richer schema from the
        # handler annotation (e.g. ``/users/{id}`` + ``id: int``).
        if convertor not in ("str", "path"):
            continue
        schema = _json_schema_from_annotation(hints[name], defs)
        if schema is not None:
            parameters[name]["schema"] = schema

    return list(parameters.values())


def _query_parameters(endpoint: Any, defs: dict | None = None) -> list[dict]:
    """OpenAPI ``parameters`` entries from a route's ``params_model``."""
    params_model = getattr(endpoint, "_params_model", None)
    if params_model is None:
        return []
    schema = params_model.model_json_schema(ref_template="#/components/schemas/{model}")
    _hoist_defs(schema, defs)
    required = set(schema.get("required", []))
    parameters = []
    for name, prop in schema.get("properties", {}).items():
        prop = {k: v for k, v in prop.items() if k != "title"}
        parameters.append(
            {"name": name, "in": "query", "required": name in required, "schema": prop}
        )
    return parameters


def _is_pydantic_model(obj):
    """Check if obj is a Pydantic model class."""
    try:
        from pydantic import BaseModel

        return isinstance(obj, type) and issubclass(obj, BaseModel)
    except ImportError:
        return False


def _is_parametrized_generic(obj):
    """Whether obj is a parametrized generic model (e.g. ``Page[Item]``).

    Its ``__name__`` carries brackets, which are invalid as an OpenAPI component
    key — so it's emitted inline via the generic schema path instead of a $ref.
    """
    meta = getattr(obj, "__pydantic_generic_metadata__", None)
    return bool(meta and meta.get("args"))


def _handler_hints(endpoint):
    from responder.routes import _view_type_hints

    try:
        return _view_type_hints(endpoint)
    except Exception:
        return {}


def _marker_specs(endpoint):
    if isinstance(endpoint, type):
        return ()
    from responder.params import marker_params

    try:
        return marker_params(endpoint, _handler_hints(endpoint))
    except Exception:
        return ()


def _marker_parameters(endpoint: Any, defs: dict | None = None) -> list[dict]:
    """OpenAPI parameters from Query()/Header()/Cookie() markers."""
    location_map = {"query": "query", "header": "header", "cookie": "cookie"}
    parameters = []
    for spec in _marker_specs(endpoint):
        where = location_map.get(spec.location)
        if where is None:  # path markers handled by _path_parameters
            continue
        schema = _json_schema_from_adapter(spec.adapter, defs)
        parameter = {
            "name": spec.lookup,
            "in": where,
            "required": spec.required,
            "schema": schema,
        }
        if spec.marker.description:
            parameter["description"] = spec.marker.description
        if spec.marker.deprecated:
            parameter["deprecated"] = True
        parameters.append(parameter)
    return parameters


def _is_form_model(annotation: Any) -> bool:
    """Whether a ``Form()`` annotation is a Pydantic model class.

    Duck-typed to mirror the runtime check in ``responder.routes``: a model
    annotation binds the whole parsed form, so its fields — not the parameter
    itself — are the documented form fields.
    """
    return (
        isinstance(annotation, type)
        and hasattr(annotation, "model_validate")
        and hasattr(annotation, "model_fields")
    )


def _is_upload_file(cls: type) -> bool:
    try:
        from starlette.datastructures import UploadFile
    except ImportError:  # pragma: no cover - starlette is a core dep
        return False
    return issubclass(cls, UploadFile)


def _model_has_upload_field(model: Any) -> bool:
    """Whether any of a form model's fields carries an ``UploadFile``.

    An upload field means the form can only arrive as ``multipart/form-data``
    (urlencoded bodies cannot carry files), matching how a per-field ``File()``
    marker selects the media type.
    """
    from typing import get_args

    def has_upload(annotation: Any) -> bool:
        if isinstance(annotation, type):
            return _is_upload_file(annotation)
        return any(has_upload(arg) for arg in get_args(annotation))

    try:
        return any(has_upload(field.annotation) for field in model.model_fields.values())
    except Exception:
        return False


_UPLOAD_TOLERANT_GENERATOR: Any = None


def _upload_tolerant_generator() -> Any:
    """A ``GenerateJsonSchema`` subclass documenting ``UploadFile`` fields.

    Starlette's ``UploadFile`` is an arbitrary type with no JSON schema, so
    Pydantic's generator raises on it; this renders such fields as binary
    strings instead — the OpenAPI convention for multipart file parts.
    """
    global _UPLOAD_TOLERANT_GENERATOR  # noqa: PLW0603 - lazy import cache
    if _UPLOAD_TOLERANT_GENERATOR is None:
        from pydantic.json_schema import GenerateJsonSchema

        class _UploadTolerant(GenerateJsonSchema):
            def handle_invalid_for_json_schema(self, schema, error_info):
                cls = schema.get("cls") if isinstance(schema, dict) else None
                if isinstance(cls, type) and _is_upload_file(cls):
                    return {"type": "string", "format": "binary"}
                return super().handle_invalid_for_json_schema(schema, error_info)

        _UPLOAD_TOLERANT_GENERATOR = _UploadTolerant
    return _UPLOAD_TOLERANT_GENERATOR


def _form_model_schema(model: Any, downconvert: bool, defs: dict | None) -> dict:
    """A form model's JSON schema, with its ``$defs`` hoisted into ``defs``."""
    try:
        schema = model.model_json_schema(
            ref_template="#/components/schemas/{model}",
            schema_generator=_upload_tolerant_generator(),
        )
    except Exception:
        return {"type": "object"}
    _hoist_defs(schema, defs)
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        if isinstance(prop, dict):
            prop.pop("title", None)
    return _adapt_schema(schema, downconvert)


def _form_request_body(
    endpoint: Any, downconvert: bool, defs: dict | None = None
) -> dict | None:
    """A requestBody schema built from Form()/File() markers, or None.

    A Pydantic-model ``Form()`` parameter binds the whole form, so it
    contributes the model's own JSON schema; scalar markers contribute
    per-field properties. File fields are ``{type: string, format: binary}``;
    the media type is ``multipart/form-data`` when any file is present, else
    urlencoded.
    """
    model_specs = []
    field_specs = []
    for spec in _marker_specs(endpoint):
        if spec.location == "form" and _is_form_model(spec.annotation):
            model_specs.append(spec)
        elif spec.location in ("form", "file"):
            field_specs.append(spec)
    if not model_specs and not field_specs:
        return None
    has_file = any(s.location == "file" for s in field_specs) or any(
        _model_has_upload_field(s.annotation) for s in model_specs
    )
    body_required = any(s.required for s in (*model_specs, *field_specs))

    if len(model_specs) == 1 and not field_specs:
        # The common case: the model *is* the form body.
        obj = _form_model_schema(model_specs[0].annotation, downconvert, defs)
    else:
        properties: dict = {}
        required: list = []
        for spec in model_specs:
            mschema = _form_model_schema(spec.annotation, downconvert, defs)
            properties.update(mschema.get("properties", {}))
            if spec.required:
                required.extend(
                    name for name in mschema.get("required", []) if name not in required
                )
        for spec in field_specs:
            if spec.location == "file":
                file_schema = {"type": "string", "format": "binary"}
                schema = (
                    {"type": "array", "items": file_schema}
                    if spec.is_sequence
                    else file_schema
                )
            else:
                schema = _adapt_schema(
                    _json_schema_from_adapter(spec.adapter, defs), downconvert
                )
            properties[spec.lookup] = schema
            if spec.required and spec.lookup not in required:
                required.append(spec.lookup)
        obj = {"type": "object", "properties": properties}
        if required:
            obj["required"] = required

    media_type = (
        "multipart/form-data" if has_file else "application/x-www-form-urlencoded"
    )
    body: dict = {"content": {media_type: {"schema": obj}}}
    if body_required:
        body["required"] = True
    return body


def _body_model(endpoint, route=None, dep_names=()):
    """The request-body Pydantic model inferred from a handler parameter.

    The inference mirrors the runtime body-injection exclusions
    (``Route.__call__``): a parameter is only the body model if it isn't a path
    parameter, a registered dependency, an auth injection, or a defaulted/marker
    parameter — so the generated schema never documents a body the handler
    doesn't read.
    """
    import inspect

    if isinstance(endpoint, type):
        return None
    sig: Any
    try:
        sig = inspect.signature(endpoint).parameters
    except (TypeError, ValueError):
        sig = {}
    path_names = set(getattr(route, "param_convertors", {})) if route else set()
    route_auth = getattr(endpoint, "_route_auth", ())
    if route is not None:
        route_auth = route_auth or getattr(route.endpoint, "_route_auth", ())
    auth_names = _AUTH_INJECTION_NAMES if route_auth else frozenset()
    for name, hint in _handler_hints(endpoint).items():
        if name == "return" or not _is_pydantic_model(hint):
            continue
        if name in path_names or name in dep_names or name in auth_names:
            continue
        if name in sig and sig[name].default is not inspect.Parameter.empty:
            continue
        return hint
    return None


def _response_model(endpoint):
    """Return an explicit model/opt-out or an inferred return contract."""
    unset = object()
    explicit = getattr(endpoint, "_response_model", unset)
    if explicit is not unset:
        return explicit
    if not isinstance(endpoint, type):
        from responder.routes import _view_return_hint

        return_hint = _view_return_hint(endpoint)
        return inferred_response_model(return_hint)
    return None


def _response_models(route: Any, endpoint: Any, op_endpoint: Any) -> dict[int, Any]:
    """Return route-level status contracts plus method-level overrides."""
    models = dict(getattr(route, "_response_models", {}) or {})
    if op_endpoint is not endpoint:
        models.update(getattr(op_endpoint, "_response_models", {}) or {})
    return models


def _operation_endpoint(endpoint, method):
    """The callable that implements a method on a function or class endpoint."""
    if isinstance(endpoint, type):
        return getattr(endpoint, f"on_{method}", endpoint)
    return endpoint


def _operation_attr(endpoint, op_endpoint, name, default=None):
    """Route-level metadata with optional method-level override."""
    return getattr(op_endpoint, name, getattr(endpoint, name, default))


def _operation_meta(endpoint, op_endpoint):
    """Merge route-level and method-level OpenAPI metadata."""
    meta = getattr(endpoint, "_openapi_meta", None)
    op_meta = getattr(op_endpoint, "_openapi_meta", None)
    if meta and op_meta:
        return _deep_merge(meta, op_meta)
    return op_meta or meta


def _identifier(value: str, *, fallback: str = "operation") -> str:
    ident = re.sub(r"[^0-9a-zA-Z_]+", "_", value).strip("_")
    if not ident:
        return fallback
    if ident[0].isdigit():
        ident = f"{fallback}_{ident}"
    return ident


def _titleize(value: str) -> str:
    return " ".join(part.capitalize() for part in _identifier(value).split("_"))


def _first_path_tag(path: str) -> str | None:
    for part in path.strip("/").split("/"):
        if part and not part.startswith("{"):
            return part.replace("-", " ").replace("_", " ").title()
    return None


def _default_operation_id(method: str, path: str, used: set[str]) -> str:
    pieces = [method, *(p for p in re.split(r"[/{}:.-]+", path) if p)]
    base = _identifier("_".join(pieces).lower(), fallback=f"{method}_operation")
    name = base
    index = 2
    while name in used:
        name = f"{base}_{index}"
        index += 1
    used.add(name)
    return name


def _apply_problem_responses(
    op: dict,
    *,
    has_validation: bool,
    secured: bool,
    csrf_protected: bool,
    body_limited: bool,
    rate_limited: bool,
    timed: bool,
    problem_details: bool,
) -> None:
    response = _problem_response if problem_details else _legacy_error_response
    for status, description in _COMMON_PROBLEM_STATUSES.items():
        op["responses"].setdefault(status, response(description))
    if body_limited:
        op["responses"].setdefault("413", response("Content Too Large"))
    if has_validation:
        op["responses"].setdefault("422", response("Validation Error", validation=True))
    if secured:
        op["responses"].setdefault("401", response("Not Authenticated"))
        op["responses"].setdefault("403", response("Forbidden"))
    elif csrf_protected:
        op["responses"].setdefault("403", response("Forbidden"))
    if rate_limited:
        op["responses"].setdefault("429", response("Too Many Requests"))
        op["responses"].setdefault("503", response("Service Unavailable"))
    if timed:
        op["responses"].setdefault("504", response("Gateway Timeout"))


def _csrf_protected(app: Any, endpoint: Any, op_endpoint: Any, method: str) -> bool:
    if method.upper() in _CSRF_SAFE_METHODS:
        return False
    route_csrf = _operation_attr(endpoint, op_endpoint, "_csrf")
    if route_csrf is None:
        return bool(getattr(getattr(app, "router", None), "csrf", False))
    return bool(route_csrf)


def _rate_limited(app: Any, endpoint: Any, op_endpoint: Any) -> bool:
    return bool(
        getattr(app, "_openapi_rate_limited", False)
        or _operation_attr(endpoint, op_endpoint, "_rate_limited", False)
    )


def _doc_methods(route: Any, has_body: bool = False) -> list[str]:
    """Lowercased HTTP methods to document for a route (no HEAD/OPTIONS)."""
    methods = getattr(route, "methods", None)
    if methods:
        return sorted(m.lower() for m in methods if m.upper() not in ("HEAD", "OPTIONS"))
    endpoint = route.endpoint
    if isinstance(endpoint, type):
        verbs = ("get", "post", "put", "patch", "delete")
        found = [v for v in verbs if hasattr(endpoint, f"on_{v}")]
        if found:
            return found
    # A methods-less route carrying a request body is meant for POST.
    return ["post"] if has_body else ["get"]


def _has_param_validation(endpoint: Any) -> bool:
    """Whether the route validates query/marker params (applies to any method)."""
    return bool(getattr(endpoint, "_params_model", None) or _marker_specs(endpoint))


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` onto ``base`` (override wins)."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _rewrite_refs(obj):
    """Rewrite Pydantic's default ``#/$defs/X`` refs to ``#/components/schemas/X``."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key == "$ref" and isinstance(value, str) and value.startswith("#/$defs/"):
                out[key] = "#/components/schemas/" + value[len("#/$defs/") :]
            else:
                out[key] = _rewrite_refs(value)
        return out
    if isinstance(obj, list):
        return [_rewrite_refs(item) for item in obj]
    return obj


def _downconvert_30(obj):
    """Best-effort down-convert a Pydantic (JSON Schema 2020-12) fragment to the
    OpenAPI 3.0 dialect.

    OpenAPI 3.1 is a superset of 2020-12 and needs no conversion, but 3.0
    predates it and rejects ``{"type": "null"}`` and array-valued ``examples``.
    So collapse ``anyOf``/``oneOf`` null-unions (what ``Optional[...]`` emits)
    into ``nullable`` and singularize an ``examples`` array into ``example``.
    """
    if isinstance(obj, list):
        return [_downconvert_30(item) for item in obj]
    if not isinstance(obj, dict):
        return obj

    obj = {key: _downconvert_30(value) for key, value in obj.items()}

    for key in ("anyOf", "oneOf"):
        variants = obj.get(key)
        if not isinstance(variants, list):
            continue
        non_null = [v for v in variants if v != {"type": "null"}]
        if len(non_null) == len(variants):
            continue  # no null branch to fold in
        obj["nullable"] = True
        del obj[key]
        if len(non_null) == 1:
            branch = non_null[0]
            # A bare $ref ignores sibling keywords in 3.0, so wrap it in allOf.
            if "$ref" in branch and len(branch) == 1:
                obj["allOf"] = [branch]
            else:
                for bkey, bvalue in branch.items():
                    obj.setdefault(bkey, bvalue)
        elif non_null:
            obj[key] = non_null

    examples = obj.get("examples")
    if isinstance(examples, list) and examples:
        obj.setdefault("example", examples[0])
        del obj["examples"]

    return obj


def _adapt_schema(schema, downconvert):
    """Point refs at component schemas and, for 3.0, down-convert the dialect."""
    schema = _rewrite_refs(schema)
    if downconvert:
        schema = _downconvert_30(schema)
    return schema


def _openapi_schema_for(tp, downconvert):
    """OpenAPI schema for a generic type (``list[Model]``, unions, …) plus the
    nested component ``$defs`` it references, both dialect-adapted."""
    from pydantic import TypeAdapter

    json_schema = TypeAdapter(tp).json_schema(ref_template="#/components/schemas/{model}")
    defs = json_schema.pop("$defs", {})
    schema = _adapt_schema(json_schema, downconvert)
    schema.pop("title", None)
    defs = {name: _adapt_schema(d, downconvert) for name, d in defs.items()}
    for d in defs.values():
        d.pop("title", None)
    return schema, defs


def _normalize_security(security: Any) -> list[dict]:
    """Normalize a route's ``security`` into OpenAPI requirement objects.

    Accepts a bare scheme name, a list of names, or a list of requirement dicts
    (``["bearerAuth"]`` and ``[{"bearerAuth": []}]`` are equivalent).
    """
    if isinstance(security, (str, dict)):
        security = [security]
    requirements = []
    for item in security:
        requirements.append({item: []} if isinstance(item, str) else item)
    return requirements


class PydanticPlugin:
    """APISpec plugin that resolves Pydantic models to JSON Schema."""

    def __init__(self):
        self._schemas = {}

    def definition_helper(self, name, definition, **kwargs):
        schema = kwargs.get("schema")
        if schema is not None and _is_pydantic_model(schema):
            return schema.model_json_schema()
        return None

    def resolve_schemas(self, spec):
        pass

    def init_spec(self, spec):
        pass

    def operation_helper(self, **kwargs):
        return {}


class OpenAPISchema:
    def __init__(
        self,
        app,
        title,
        version,
        plugins=None,
        description=None,
        terms_of_service=None,
        contact=None,
        license=None,  # noqa: A002
        openapi=None,
        openapi_route="/schema.yml",
        docs_route="/docs/",
        static_route="/static",
        openapi_theme=DEFAULT_OPENAPI_THEME,
        servers=None,
    ):
        self.app = app
        self.servers = servers
        self.schemas = {}
        self.pydantic_schemas = {}
        self.security_schemes: dict[str, dict] = {}
        self.default_security: list[dict] = []
        # Cached generated document: {"key": ..., "spec": APISpec, "yaml": str|None}.
        # Rebuilt when the cache key (route-table generation + registered
        # schema/security counts) changes; see ``_spec_cache_key``.
        self._spec_cache: dict | None = None
        self.title = title or "Responder API"
        self.version = version or "0.0.0"
        self.description = description
        self.terms_of_service = terms_of_service
        self.contact = contact
        self.license = license

        # ``docs_route`` promises "Enables OpenAPI if not already set": without a
        # version there is no schema route and the docs UI points at a 404, so
        # default to the newest supported version.
        implied_openapi = openapi is None and docs_route is not None
        if implied_openapi:
            openapi = "3.1.0"
        self.openapi_version = openapi
        self.openapi_route = openapi_route

        self.docs_theme = (
            openapi_theme if openapi_theme in API_THEMES else DEFAULT_OPENAPI_THEME
        )
        self.docs_route = docs_route

        self.plugins = [MarshmallowPlugin()] if plugins is None else plugins

        if self.openapi_version is not None:
            if implied_openapi and self._route_at(self.openapi_route) is not None:
                # On 8.0.0, ``docs_route`` alone registered no schema route, so
                # apps served their own; a patch must not break them.
                logger.warning(
                    "docs_route implies an OpenAPI schema route, but a route "
                    "already exists at %r; keeping it (the docs page will use "
                    "it as its schema).",
                    self.openapi_route,
                )
            else:
                self.app.add_route(self.openapi_route, self.schema_response)
                if implied_openapi:
                    self._yield_schema_route_to_user_routes()

        if self.docs_route is not None:
            self.app.add_route(self.docs_route, self.docs_response)

        theme_path = (Path(__file__).parent / "docs").resolve()
        self.templates = Templates(directory=theme_path)

        self.static_route = static_route

    def _route_at(self, path):
        """The registered route at ``path``, if any."""
        for route in getattr(self.app.router, "routes", []):
            if getattr(route, "route", None) == path:
                return route
        return None

    def _yield_schema_route_to_user_routes(self):
        """Let a later user route at the schema path replace the implied one.

        On 8.0.0 ``docs_route`` alone registered no schema route, so apps were
        free to register their own handler at ``openapi_route``. The implied
        schema route must not turn that registration into a duplicate-route
        error, so the router's ``add_route`` is wrapped (per instance): a
        later registration at the schema path evicts the implied route — with
        a logged warning — and the docs page then points at the user's route.
        """
        router = self.app.router
        schema_path = self.openapi_route
        schema_endpoint = self.schema_response
        original_add_route = router.add_route

        def add_route(route=None, endpoint=None, **kwargs):
            if (
                route == schema_path
                and endpoint is not None
                and not kwargs.get("before_request")
            ):
                for existing in list(router.routes):
                    if (
                        getattr(existing, "route", None) == schema_path
                        and existing.endpoint == schema_endpoint
                    ):
                        router.routes.remove(existing)
                        cache = getattr(router, "_route_cache", None)
                        if cache is not None:
                            cache.clear()
                        logger.warning(
                            "A route was registered at %r, which the docs "
                            "page uses for its implied OpenAPI schema; the "
                            "generated schema route yields to it.",
                            schema_path,
                        )
            return original_add_route(route, endpoint, **kwargs)

        # setattr keeps this an instance-level override of the bound method.
        setattr(router, "add_route", add_route)  # noqa: B010

    def _spec_cache_key(self):
        """A cheap fingerprint of everything the generated document depends on.

        The router bumps ``_generation`` whenever its route table changes;
        the remaining terms catch schema/security registrations (including
        same-name replacements, which ``add_schema``/``add_security_scheme``
        handle by dropping the cache outright).
        """
        router = getattr(self.app, "router", None)
        return (
            getattr(router, "_generation", None),
            len(getattr(router, "routes", ()) or ()),
            len(getattr(router, "dependencies", {}) or {}),
            getattr(router, "max_request_size", None),
            getattr(router, "request_timeout", None),
            getattr(router, "csrf", False),
            getattr(self.app, "problem_details", True),
            getattr(self.app, "_openapi_rate_limited", False),
            len(self.schemas),
            len(self.pydantic_schemas),
            len(self.security_schemes),
            len(self.default_security),
        )

    @property
    def _apispec(self):
        """The generated ``APISpec``, cached until routes or schemas change.

        Building the spec walks every route and regenerates every Pydantic
        JSON schema, so the result is cached and served to ``schema_response``
        and the docs UI; adding a route, schema, or security scheme
        invalidates it.
        """
        key = self._spec_cache_key()
        cached = self._spec_cache
        if cached is not None and cached["key"] == key:
            return cached["spec"]
        spec = self._build_apispec()
        self._spec_cache = {"key": key, "spec": spec, "yaml": None}
        return spec

    def _build_apispec(self):
        info = {}
        if self.description is not None:
            info["description"] = self.description
        if self.terms_of_service is not None:
            info["termsOfService"] = self.terms_of_service
        if self.contact is not None:
            info["contact"] = self.contact
        if self.license is not None:
            info["license"] = self.license

        extra_options = {"servers": self.servers} if self.servers else {}
        spec = APISpec(
            title=self.title,
            version=self.version,
            openapi_version=self.openapi_version,
            plugins=self.plugins,
            info=info,
            **extra_options,
        )

        skip_paths = {self.openapi_route}
        if self.docs_route:
            skip_paths.add(self.docs_route)
            skip_paths.add(self.docs_route.rstrip("/"))
        skip_names = {
            "_static_response",
            "_metrics_view",
            "schema_response",
            "docs_response",
        }
        dep_names = set(getattr(self.app.router, "dependencies", {}) or {})
        downconvert = str(self.openapi_version or "").startswith("3.0")

        auto_models: dict[str, Any] = {}
        auto_def_schemas: dict[str, dict] = {}
        param_defs: dict[str, dict] = {}
        used_operation_ids: set[str] = set()

        def remember_model(model):
            if (
                model is None
                or not _is_pydantic_model(model)
                or _is_parametrized_generic(model)
            ):
                return
            existing = auto_models.get(model.__name__)
            if existing is not None and existing is not model:
                logger.warning(
                    "OpenAPI component name collision: two distinct models "
                    "are both named %r (%s vs %s); the schema served for "
                    "one will be wrong. Rename one of them.",
                    model.__name__,
                    getattr(existing, "__module__", "?"),
                    getattr(model, "__module__", "?"),
                )
            auto_models[model.__name__] = model

        # Imported here (not at module scope) to avoid a circular import:
        # responder.routes is only needed once a schema is actually built.
        from responder.routes import WebSocketRoute

        for route in self.app.router.routes:
            # WebSocket endpoints don't speak HTTP; documenting them as GET
            # operations produces a spec full of operations that can't exist.
            if isinstance(route, WebSocketRoute):
                continue
            endpoint = route.endpoint
            if getattr(endpoint, "_include_in_schema", True) is False:
                continue
            ep_name = getattr(endpoint, "__name__", type(endpoint).__name__)
            if ep_name in skip_names:
                continue
            # OpenAPI paths use plain `{id}` templates, not `{id:int}` patterns.
            path = getattr(route, "path_template", route.route)
            if path in skip_paths:
                continue
            body_verbs = ("post", "put", "patch", "delete")

            # Auto-generate one operation per method from the route's models.
            auto_ops: dict[str, dict] = {}
            route_req_model = _body_model(endpoint, route, dep_names)
            route_form_body = _form_request_body(endpoint, downconvert, param_defs)
            route_has_any_body = (
                route_req_model is not None or route_form_body is not None
            )
            for method in _doc_methods(route, has_body=route_has_any_body):
                op_endpoint = _operation_endpoint(endpoint, method)
                parameters = (
                    _path_parameters(route, op_endpoint, param_defs)
                    + _query_parameters(endpoint, param_defs)
                    + (
                        []
                        if op_endpoint is endpoint
                        else _query_parameters(op_endpoint, param_defs)
                    )
                    + _marker_parameters(op_endpoint, param_defs)
                )
                for parameter in parameters:
                    if "schema" in parameter:
                        parameter["schema"] = _adapt_schema(
                            parameter["schema"], downconvert
                        )

                req_model = _body_model(op_endpoint, route, dep_names) or route_req_model
                resp_model = _response_model(op_endpoint)
                response_source = op_endpoint
                if resp_model is None and op_endpoint is not endpoint:
                    resp_model = _response_model(endpoint)
                    response_source = endpoint
                explicit_response_model = hasattr(response_source, "_response_model")
                if resp_model is False:
                    resp_model = None
                status_response_models = _response_models(route, endpoint, op_endpoint)
                stream_mode = getattr(route, "_stream_mode", None)
                stream_model = getattr(route, "_stream_model", None)
                if stream_mode is not None:
                    resp_model = None
                    explicit_response_model = False
                for model in (
                    req_model,
                    resp_model,
                    stream_model,
                    *status_response_models.values(),
                ):
                    remember_model(model)

                # The response schema: a $ref for a single model, or an inline
                # array/oneOf (with its nested models hoisted) for a generic.
                resp_schema = None
                if resp_model is not None:
                    if _is_pydantic_model(resp_model) and not _is_parametrized_generic(
                        resp_model
                    ):
                        resp_schema = {
                            "$ref": f"#/components/schemas/{resp_model.__name__}"
                        }
                    else:
                        resp_schema, resp_defs = _openapi_schema_for(
                            resp_model, downconvert
                        )
                        auto_def_schemas.update(resp_defs)

                stream_schema = None
                if stream_model is not None:
                    if _is_pydantic_model(
                        stream_model
                    ) and not _is_parametrized_generic(stream_model):
                        stream_schema = {
                            "$ref": f"#/components/schemas/{stream_model.__name__}"
                        }
                    else:
                        stream_schema, stream_defs = _openapi_schema_for(
                            stream_model, downconvert
                        )
                        auto_def_schemas.update(stream_defs)

                status_response_schemas: dict[int, dict[str, Any]] = {}
                for response_status, model in status_response_models.items():
                    if _is_pydantic_model(model) and not _is_parametrized_generic(model):
                        status_response_schemas[response_status] = {
                            "$ref": f"#/components/schemas/{model.__name__}"
                        }
                    else:
                        response_schema, response_defs = _openapi_schema_for(
                            model, downconvert
                        )
                        status_response_schemas[response_status] = response_schema
                        auto_def_schemas.update(response_defs)

                # The request-body schema mirrors the response: a $ref for a
                # single model, inline for a parametrized generic.
                req_schema = None
                if req_model is not None:
                    if _is_pydantic_model(req_model) and not _is_parametrized_generic(
                        req_model
                    ):
                        req_schema = {
                            "$ref": f"#/components/schemas/{req_model.__name__}"
                        }
                    else:
                        req_schema, req_defs = _openapi_schema_for(req_model, downconvert)
                        auto_def_schemas.update(req_defs)

                has_param_validation = bool(
                    _operation_attr(endpoint, op_endpoint, "_params_model")
                    or _marker_specs(op_endpoint)
                )
                form_body = (
                    _form_request_body(op_endpoint, downconvert, param_defs)
                    or route_form_body
                )
                route_security = _operation_attr(endpoint, op_endpoint, "_security")
                op_meta = _operation_meta(endpoint, op_endpoint)
                has_any_body = req_model is not None or form_body is not None

                op: dict[str, Any] = {}
                # The success response is keyed under the route's declared
                # ``status_code=`` (defaulting to 200); statuses for which HTTP
                # forbids content carry no response schema.
                default_status = _operation_attr(
                    endpoint, op_endpoint, "_default_status_code"
                )
                success_status = (
                    str(default_status) if default_status is not None else "200"
                )
                ok: dict[str, Any] = {"description": "Successful response"}
                if stream_mode is not None and response_status_allows_body(
                    int(success_status)
                ):
                    media_type = (
                        "text/event-stream"
                        if stream_mode == "sse"
                        else "application/x-ndjson"
                    )
                    media: dict[str, Any] = {"schema": {"type": "string"}}
                    if stream_schema is not None:
                        media["x-responder-item-schema"] = dict(stream_schema)
                    ok["content"] = {media_type: media}
                    ok["x-responder-stream"] = {
                        "mode": stream_mode,
                        "itemSchema": dict(stream_schema or {}),
                    }
                elif resp_schema is not None and response_status_allows_body(
                    int(success_status)
                ):
                    response_content_type = (
                        "application/json"
                        if explicit_response_model
                        else response_media_type(resp_model)
                    )
                    ok["content"] = {response_content_type: {"schema": dict(resp_schema)}}
                op["responses"] = {success_status: ok}
                for response_status, response_schema in status_response_schemas.items():
                    op["responses"][str(response_status)] = {
                        "description": status_title(response_status),
                        "content": {
                            "application/json": {"schema": dict(response_schema)}
                        },
                    }
                has_body = has_any_body and method in body_verbs
                if has_body and form_body is not None:
                    # Form/file upload body (multipart or urlencoded).
                    op["requestBody"] = {
                        "content": dict(form_body["content"]),
                    }
                    if form_body.get("required"):
                        op["requestBody"]["required"] = True
                elif has_body and req_schema is not None:
                    # _body_model only infers a body from a parameter without a
                    # default, so an inferred JSON body is always required.
                    op["requestBody"] = {
                        "content": {"application/json": {"schema": dict(req_schema)}},
                        "required": True,
                    }
                if route_security is not None:
                    op["security"] = _normalize_security(route_security)
                elif self.default_security:
                    op["security"] = [dict(req) for req in self.default_security]
                if op_meta:
                    op = _deep_merge(op, op_meta)
                if "operationId" not in op:
                    op["operationId"] = _default_operation_id(
                        method, path, used_operation_ids
                    )
                else:
                    used_operation_ids.add(str(op["operationId"]))
                if "summary" not in op:
                    op["summary"] = _titleize(str(op["operationId"]))
                if "tags" not in op:
                    tag = _first_path_tag(path)
                    if tag is not None:
                        op["tags"] = [tag]
                secured = bool(op.get("security"))
                _apply_problem_responses(
                    op,
                    has_validation=has_body or has_param_validation,
                    secured=secured,
                    csrf_protected=_csrf_protected(
                        self.app, endpoint, op_endpoint, method
                    ),
                    body_limited=getattr(self.app.router, "max_request_size", None)
                    is not None,
                    rate_limited=_rate_limited(self.app, endpoint, op_endpoint),
                    timed=getattr(self.app.router, "request_timeout", None) is not None,
                    problem_details=getattr(self.app, "problem_details", True),
                )
                # Parameters live on the operation, not the path item: multiple
                # methods can share a path (e.g. @api.get + @api.post on one
                # path), and each must carry only its own params.
                if parameters:
                    op["parameters"] = [dict(p) for p in parameters]
                auto_ops[method] = op

            # Docstring YAML overrides / enriches the generated base.
            doc_ops = {}
            if route.description:
                doc_ops = (
                    yaml_utils.load_operations_from_docstring(route.description) or {}
                )
            operations = _deep_merge(auto_ops, doc_ops)
            if operations:
                spec.path(path=path, operations=operations)

        # Register marshmallow schemas
        for name, schema in self.schemas.items():
            spec.components.schema(name, schema=schema)

        # Register Pydantic schemas (explicit + auto-discovered from routes).
        # Nested models land in Pydantic's ``$defs``; hoist each into its own
        # top-level component and point the refs there so the document resolves.
        registered = set(self.schemas)
        if (
            getattr(self.app, "problem_details", True)
            and "ProblemDetails" not in registered
        ):
            spec.components.schema("ProblemDetails", component=_problem_details_schema())
            registered.add("ProblemDetails")
        for name, model in {**auto_models, **self.pydantic_schemas}.items():
            if name in registered:
                continue
            json_schema = model.model_json_schema(
                ref_template="#/components/schemas/{model}"
            )
            defs = json_schema.pop("$defs", {})
            # A self-referential model returns a {$ref (+ $defs)} wrapper rather
            # than its body; register the real definition so the component isn't
            # an empty self-pointer (which breaks Swagger UI / codegen).
            if name in defs and set(json_schema) <= {"$ref", "allOf"}:
                json_schema = defs.pop(name)
            json_schema = _adapt_schema(json_schema, downconvert)
            json_schema.pop("title", None)
            spec.components.schema(name, component=json_schema)
            registered.add(name)
            for def_name, def_schema in defs.items():
                if def_name in registered:
                    continue
                def_schema = _adapt_schema(def_schema, downconvert)
                def_schema.pop("title", None)
                spec.components.schema(def_name, component=def_schema)
                registered.add(def_name)

        # Register definitions hoisted out of parameter/form schemas (enums,
        # nested models referenced by Query()/Header()/params_model fields) so
        # their ``#/components/schemas/...`` refs don't dangle.
        for def_name, def_schema in param_defs.items():
            if def_name in auto_def_schemas:
                continue
            def_schema = _adapt_schema(def_schema, downconvert)
            def_schema.pop("title", None)
            auto_def_schemas[def_name] = def_schema

        # Register models hoisted from generic response schemas (list/union).
        for def_name, def_schema in auto_def_schemas.items():
            if def_name not in registered:
                spec.components.schema(def_name, component=def_schema)
                registered.add(def_name)

        # Register security schemes (enables Swagger's Authorize button).
        for sec_name, sec_scheme in self.security_schemes.items():
            spec.components.security_scheme(sec_name, sec_scheme)

        return spec

    @property
    def openapi(self):
        spec = self._apispec  # refreshes the cache if it went stale
        cached = self._spec_cache
        if cached is None:  # pragma: no cover - _apispec always fills the cache
            return spec.to_yaml()
        if cached["yaml"] is None:
            cached["yaml"] = spec.to_yaml()
        return cached["yaml"]

    def add_security_scheme(self, name, scheme, *, default=False):
        """Register an OpenAPI security scheme (and optionally require it globally).

        Re-registering the same ``name`` with an identical scheme is a no-op
        (``route()`` re-registers a route's scheme on every request). Registering
        a *different* scheme under an already-used name is a configuration error
        — otherwise, e.g. two ``OAuth2Auth`` instances that both default to
        ``scheme_name="oauth2Auth"`` but declare different flows would silently
        collapse into one.
        """
        existing = self.security_schemes.get(name)
        if existing is not None and existing != scheme:
            raise ValueError(
                f"Security scheme '{name}' is already registered with a "
                "different definition. Give one of the conflicting schemes a "
                "distinct scheme_name= so they don't overwrite each other."
            )
        self.security_schemes[name] = scheme
        if default:
            requirement: dict = {name: []}
            if requirement not in self.default_security:
                self.default_security.append(requirement)
        # A same-name re-registration leaves the counts unchanged, so the
        # cache key can't catch it; drop the cached document outright.
        self._spec_cache = None

    def add_schema(self, name, schema, check_existing=True):
        """Adds a marshmallow or Pydantic schema to the API specification."""
        if check_existing:
            if name in self.schemas or name in self.pydantic_schemas:
                raise ValueError(f"Schema '{name}' is already registered")

        if _is_pydantic_model(schema):
            self.pydantic_schemas[name] = schema
        else:
            self.schemas[name] = schema
        # Same-name replacement (check_existing=False) keeps the counts
        # unchanged, so the cache key can't catch it; drop the cache outright.
        self._spec_cache = None

    def schema(self, name, **options):
        """Decorator for registering schemas (marshmallow or Pydantic).

        Usage::

            from marshmallow import Schema, fields

            @api.schema("Pet")
            class PetSchema(Schema):
                name = fields.Str()

        Or with Pydantic::

            from pydantic import BaseModel

            @api.schema("Pet")
            class Pet(BaseModel):
                name: str
                age: int = 0

        """

        def decorator(f):
            self.add_schema(name=name, schema=f, **options)
            return f

        return decorator

    @property
    def docs(self):
        return self.templates.render(
            f"{self.docs_theme}.html",
            title=self.title,
            version=self.version,
            schema_url=self.openapi_route,
        )

    def static_url(self, asset):
        """Given a static asset, return its URL path."""
        if self.static_route is None:
            raise RuntimeError("Cannot generate static URL: static_route is disabled")
        return f"{self.static_route}/{str(asset)}"

    def docs_response(self, req, resp):
        resp.html = self.docs

    def schema_response(self, req, resp):
        resp.status_code = status_codes.HTTP_200
        # Serve JSON when asked (Accept header or a .json schema route);
        # YAML otherwise.
        if self.openapi_route.endswith(".json") or "json" in req.headers.get(
            "Accept", ""
        ):
            resp.media = self._apispec.to_dict()
        else:
            resp.headers["Content-Type"] = "application/yaml"
            resp.content = self.openapi
