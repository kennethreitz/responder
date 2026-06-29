import logging
from pathlib import Path
from typing import Any

from apispec import APISpec, yaml_utils
from apispec.ext.marshmallow import MarshmallowPlugin

from responder import status_codes
from responder.statics import API_THEMES, DEFAULT_OPENAPI_THEME
from responder.templates import Templates

logger = logging.getLogger("responder.openapi")

# JSON Schema types for route path convertors.
_CONVERTOR_SCHEMAS = {
    int: {"type": "integer"},
    float: {"type": "number"},
    str: {"type": "string"},
}


def _path_parameters(route) -> list[dict]:
    """OpenAPI ``parameters`` entries for a route's path parameters."""
    return [
        {
            "name": name,
            "in": "path",
            "required": True,
            "schema": dict(_CONVERTOR_SCHEMAS.get(convertor, {"type": "string"})),
        }
        for name, convertor in getattr(route, "param_convertors", {}).items()
    ]


def _query_parameters(endpoint) -> list[dict]:
    """OpenAPI ``parameters`` entries from a route's ``params_model``."""
    params_model = getattr(endpoint, "_params_model", None)
    if params_model is None:
        return []
    schema = params_model.model_json_schema()
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


def _marker_parameters(endpoint) -> list[dict]:
    """OpenAPI parameters from Query()/Header()/Cookie() markers."""
    location_map = {"query": "query", "header": "header", "cookie": "cookie"}
    parameters = []
    for spec in _marker_specs(endpoint):
        where = location_map.get(spec.location)
        if where is None:  # path markers handled by _path_parameters
            continue
        schema = {"type": "string"}
        if spec.adapter is not None:
            try:
                schema = spec.adapter.json_schema()
                schema.pop("title", None)
            except Exception:
                schema = {"type": "string"}
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


def _form_request_body(endpoint, downconvert):
    """A requestBody schema built from Form()/File() markers, or None.

    File fields are ``{type: string, format: binary}``; the media type is
    ``multipart/form-data`` when any file is present, else urlencoded.
    """
    specs = [s for s in _marker_specs(endpoint) if s.location in ("form", "file")]
    if not specs:
        return None
    has_file = any(s.location == "file" for s in specs)
    properties: dict = {}
    required: list = []
    for spec in specs:
        if spec.location == "file":
            file_schema = {"type": "string", "format": "binary"}
            schema = (
                {"type": "array", "items": file_schema}
                if spec.is_sequence
                else file_schema
            )
        else:
            schema = {"type": "string"}
            if spec.adapter is not None:
                try:
                    schema = _adapt_schema(spec.adapter.json_schema(), downconvert)
                    schema.pop("title", None)
                except Exception:
                    schema = {"type": "string"}
        properties[spec.lookup] = schema
        if spec.required:
            required.append(spec.lookup)
    obj: dict = {"type": "object", "properties": properties}
    if required:
        obj["required"] = required
    media_type = (
        "multipart/form-data" if has_file else "application/x-www-form-urlencoded"
    )
    return {"content": {media_type: {"schema": obj}}}


def _body_model(endpoint, route=None, dep_names=()):
    """The request-body Pydantic model: explicit ``_request_model`` or an
    inferred body-model parameter.

    The inference mirrors the runtime body-injection exclusions
    (``Route.__call__``): a parameter is only the body model if it isn't a path
    parameter, a registered dependency, or a defaulted/marker parameter — so the
    generated schema never documents a body the handler doesn't read.
    """
    import inspect

    explicit = getattr(endpoint, "_request_model", None)
    if explicit is not None:
        return explicit
    if isinstance(endpoint, type):
        return None
    sig: Any
    try:
        sig = inspect.signature(endpoint).parameters
    except (TypeError, ValueError):
        sig = {}
    path_names = set(getattr(route, "param_convertors", {})) if route else set()
    for name, hint in _handler_hints(endpoint).items():
        if name == "return" or not _is_pydantic_model(hint):
            continue
        if name in path_names or name in dep_names:
            continue
        if name in sig and sig[name].default is not inspect.Parameter.empty:
            continue
        return hint
    return None


def _response_model(endpoint):
    """The response model: an explicit ``_response_model`` (a Pydantic model or a
    generic like ``list[Model]``) or a Pydantic return annotation."""
    explicit = getattr(endpoint, "_response_model", None)
    if explicit is not None:
        return explicit
    if not isinstance(endpoint, type):
        return_hint = _handler_hints(endpoint).get("return")
        if _is_pydantic_model(return_hint):
            return return_hint
    return None


def _doc_methods(route, has_body=False) -> list[str]:
    """Lowercased HTTP methods to document for a route (no HEAD/OPTIONS)."""
    methods = getattr(route, "methods", None)
    if methods:
        return sorted(
            m.lower() for m in methods if m.upper() not in ("HEAD", "OPTIONS")
        )
    endpoint = route.endpoint
    if isinstance(endpoint, type):
        verbs = ("get", "post", "put", "patch", "delete")
        found = [v for v in verbs if hasattr(endpoint, f"on_{v}")]
        if found:
            return found
    # A methods-less route carrying a request body is meant for POST.
    return ["post"] if has_body else ["get"]


def _has_param_validation(endpoint) -> bool:
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
            if (
                key == "$ref"
                and isinstance(value, str)
                and value.startswith("#/$defs/")
            ):
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

    json_schema = TypeAdapter(tp).json_schema(
        ref_template="#/components/schemas/{model}"
    )
    defs = json_schema.pop("$defs", {})
    schema = _adapt_schema(json_schema, downconvert)
    schema.pop("title", None)
    defs = {name: _adapt_schema(d, downconvert) for name, d in defs.items()}
    for d in defs.values():
        d.pop("title", None)
    return schema, defs


def _normalize_security(security) -> list[dict]:
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
        self.title = title
        self.version = version
        self.description = description
        self.terms_of_service = terms_of_service
        self.contact = contact
        self.license = license

        self.openapi_version = openapi
        self.openapi_route = openapi_route

        self.docs_theme = (
            openapi_theme if openapi_theme in API_THEMES else DEFAULT_OPENAPI_THEME
        )
        self.docs_route = docs_route

        self.plugins = [MarshmallowPlugin()] if plugins is None else plugins

        if self.openapi_version is not None:
            self.app.add_route(self.openapi_route, self.schema_response)

        if self.docs_route is not None:
            self.app.add_route(self.docs_route, self.docs_response)

        theme_path = (Path(__file__).parent / "docs").resolve()
        self.templates = Templates(directory=theme_path)

        self.static_route = static_route

    @property
    def _apispec(self):
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
        for route in self.app.router.routes:
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
            parameters = (
                _path_parameters(route)
                + _query_parameters(endpoint)
                + _marker_parameters(endpoint)
            )
            for parameter in parameters:
                if "schema" in parameter:
                    parameter["schema"] = _adapt_schema(
                        parameter["schema"], downconvert
                    )

            req_model = _body_model(endpoint, route, dep_names)
            resp_model = _response_model(endpoint)
            for model in (req_model, resp_model):
                # Parametrized generics (Page[Item]) are emitted inline via the
                # generic schema path, not registered as a named component.
                if (
                    model is not None
                    and _is_pydantic_model(model)
                    and not _is_parametrized_generic(model)
                ):
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

            # The request-body schema mirrors the response: $ref for a single
            # model, inline for a parametrized generic.
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

            has_param_validation = _has_param_validation(endpoint)
            form_body = _form_request_body(endpoint, downconvert)
            route_security = getattr(endpoint, "_security", None)
            op_meta = getattr(endpoint, "_openapi_meta", None)
            body_verbs = ("post", "put", "patch", "delete")

            # Auto-generate one operation per method from the route's models.
            auto_ops: dict[str, dict] = {}
            has_any_body = req_model is not None or form_body is not None
            for method in _doc_methods(route, has_body=has_any_body):
                op: dict[str, Any] = {}
                ok: dict[str, Any] = {"description": "Successful response"}
                if resp_schema is not None:
                    ok["content"] = {
                        "application/json": {"schema": dict(resp_schema)}
                    }
                op["responses"] = {"200": ok}
                has_body = has_any_body and method in body_verbs
                if has_body and form_body is not None:
                    # Form/file upload body (multipart or urlencoded).
                    op["requestBody"] = {
                        "content": dict(form_body["content"]),
                    }
                elif has_body and req_schema is not None:
                    op["requestBody"] = {
                        "content": {
                            "application/json": {"schema": dict(req_schema)}
                        }
                    }
                # 422 only on methods that actually validate something.
                if has_body or has_param_validation:
                    op["responses"]["422"] = {"description": "Validation error"}
                if route_security is not None:
                    op["security"] = _normalize_security(route_security)
                elif self.default_security:
                    op["security"] = [dict(req) for req in self.default_security]
                if op_meta:
                    op.update(op_meta)
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
        return self._apispec.to_yaml()

    def add_security_scheme(self, name, scheme, *, default=False):
        """Register an OpenAPI security scheme (and optionally require it globally)."""
        self.security_schemes[name] = scheme
        if default:
            requirement: dict = {name: []}
            if requirement not in self.default_security:
                self.default_security.append(requirement)

    def add_schema(self, name, schema, check_existing=True):
        """Adds a marshmallow or Pydantic schema to the API specification."""
        if check_existing:
            if name in self.schemas or name in self.pydantic_schemas:
                raise ValueError(f"Schema '{name}' is already registered")

        if _is_pydantic_model(schema):
            self.pydantic_schemas[name] = schema
        else:
            self.schemas[name] = schema

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
            resp.headers["Content-Type"] = "application/x-yaml"
            resp.content = self.openapi
