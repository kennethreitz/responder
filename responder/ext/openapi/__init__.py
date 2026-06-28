from pathlib import Path
from typing import Any

from apispec import APISpec, yaml_utils
from apispec.ext.marshmallow import MarshmallowPlugin

from responder import status_codes
from responder.statics import API_THEMES, DEFAULT_OPENAPI_THEME
from responder.templates import Templates

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
        parameters.append(
            {
                "name": spec.lookup,
                "in": where,
                "required": spec.required,
                "schema": schema,
            }
        )
    return parameters


def _body_model(endpoint):
    """The request-body Pydantic model: explicit _request_model or an injected
    body-model parameter."""
    explicit = getattr(endpoint, "_request_model", None)
    if explicit is not None:
        return explicit
    if isinstance(endpoint, type):
        return None
    for name, hint in _handler_hints(endpoint).items():
        if name == "return":
            continue
        if _is_pydantic_model(hint):
            return hint
    return None


def _response_model(endpoint):
    """The response Pydantic model: explicit _response_model or a return hint."""
    explicit = getattr(endpoint, "_response_model", None)
    if _is_pydantic_model(explicit):
        return explicit
    if not isinstance(endpoint, type):
        return_hint = _handler_hints(endpoint).get("return")
        if _is_pydantic_model(return_hint):
            return return_hint
    return None


def _doc_methods(route) -> list[str]:
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
    return ["get"]


def _route_validates(endpoint) -> bool:
    return bool(
        getattr(endpoint, "_params_model", None)
        or _body_model(endpoint)
        or _marker_specs(endpoint)
    )


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` onto ``base`` (override wins)."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


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
    ):
        self.app = app
        self.schemas = {}
        self.pydantic_schemas = {}
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

        spec = APISpec(
            title=self.title,
            version=self.version,
            openapi_version=self.openapi_version,
            plugins=self.plugins,
            info=info,
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

        auto_models: dict[str, Any] = {}
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

            req_model = _body_model(endpoint)
            resp_model = _response_model(endpoint)
            for model in (req_model, resp_model):
                if model is not None:
                    auto_models[model.__name__] = model
            validates = _route_validates(endpoint)

            # Auto-generate one operation per method from the route's models.
            auto_ops: dict[str, dict] = {}
            for method in _doc_methods(route):
                op: dict[str, Any] = {}
                ok: dict[str, Any] = {"description": "Successful response"}
                if resp_model is not None:
                    ok["content"] = {
                        "application/json": {
                            "schema": {
                                "$ref": f"#/components/schemas/{resp_model.__name__}"
                            }
                        }
                    }
                op["responses"] = {"200": ok}
                if req_model is not None and method in ("post", "put", "patch", "delete"):
                    op["requestBody"] = {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": f"#/components/schemas/{req_model.__name__}"
                                }
                            }
                        }
                    }
                if validates:
                    op["responses"]["422"] = {"description": "Validation error"}
                auto_ops[method] = op

            # Docstring YAML overrides / enriches the generated base.
            doc_ops = {}
            if route.description:
                doc_ops = (
                    yaml_utils.load_operations_from_docstring(route.description) or {}
                )
            operations = _deep_merge(auto_ops, doc_ops)
            if operations:
                spec.path(path=path, operations=operations, parameters=parameters)

        # Register marshmallow schemas
        for name, schema in self.schemas.items():
            spec.components.schema(name, schema=schema)

        # Register Pydantic schemas (explicit + auto-discovered from routes)
        registered = set(self.schemas)
        for name, model in {**auto_models, **self.pydantic_schemas}.items():
            if name in registered:
                continue
            json_schema = model.model_json_schema()
            json_schema.pop("title", None)
            spec.components.schema(name, component=json_schema)
            registered.add(name)

        return spec

    @property
    def openapi(self):
        return self._apispec.to_yaml()

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
