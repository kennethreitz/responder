from pathlib import Path

from apispec import APISpec, yaml_utils
from apispec.ext.marshmallow import MarshmallowPlugin

from responder import status_codes
from responder.statics import API_THEMES, DEFAULT_OPENAPI_THEME
from responder.templates import Templates


def _is_pydantic_model(obj):
    """Check if obj is a Pydantic model class."""
    try:
        from pydantic import BaseModel

        return isinstance(obj, type) and issubclass(obj, BaseModel)
    except ImportError:
        return False


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

        for route in self.app.router.routes:
            if route.description:
                operations = yaml_utils.load_operations_from_docstring(route.description)
                spec.path(path=route.route, operations=operations)

            # Check for Pydantic-annotated routes
            endpoint = route.endpoint
            req_model = getattr(endpoint, "_request_model", None)
            resp_model = getattr(endpoint, "_response_model", None)

            if req_model or resp_model:
                operations = {}
                methods = getattr(route, "methods", None) or ["get"]

                for method in [m.lower() for m in methods]:
                    op = {}
                    if req_model and method in ("post", "put", "patch"):
                        model_name = req_model.__name__
                        op["requestBody"] = {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": f"#/components/schemas/{model_name}"
                                    }
                                }
                            }
                        }
                    if resp_model:
                        model_name = resp_model.__name__
                        op["responses"] = {
                            "200": {
                                "description": "Successful response",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": f"#/components/schemas/{model_name}"
                                        }
                                    }
                                },
                            }
                        }
                    if op:
                        operations[method] = op

                if operations and not route.description:
                    spec.path(path=route.route, operations=operations)

        # Register marshmallow schemas
        for name, schema in self.schemas.items():
            spec.components.schema(name, schema=schema)

        # Register Pydantic schemas
        for name, model in self.pydantic_schemas.items():
            json_schema = model.model_json_schema()
            json_schema.pop("title", None)
            spec.components.schema(name, component=json_schema)

        return spec

    @property
    def openapi(self):
        return self._apispec.to_yaml()

    def add_schema(self, name, schema, check_existing=True):
        """Adds a marshmallow or Pydantic schema to the API specification."""
        if check_existing:
            assert name not in self.schemas
            assert name not in self.pydantic_schemas

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
        assert self.static_route is not None
        return f"{self.static_route}/{str(asset)}"

    def docs_response(self, req, resp):
        resp.html = self.docs

    def schema_response(self, req, resp):
        resp.status_code = status_codes.HTTP_200  # type: ignore[attr-defined]
        resp.headers["Content-Type"] = "application/x-yaml"
        resp.content = self.openapi
