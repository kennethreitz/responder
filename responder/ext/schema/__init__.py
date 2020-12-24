import os
from pathlib import Path

import apistar
import jinja2
import yaml
from apispec import APISpec, yaml_utils
from apispec.ext.marshmallow import MarshmallowPlugin

from responder.statics import DEFAULT_API_THEME
from responder.staticfiles import StaticFiles
from responder import status_codes


class Schema:
    def __init__(
        self,
        app,
        title,
        version,
        plugins=None,
        description=None,
        terms_of_service=None,
        contact=None,
        license=None,
        openapi=None,
        openapi_route="/schema.yml",
        docs_route="/docs/",
        static_route="/static",
    ):
        self.app = app
        self.schemas = {}
        self.title = title
        self.version = version
        self.description = description
        self.terms_of_service = terms_of_service
        self.contact = contact
        self.license = license

        self.openapi_version = openapi
        self.openapi_route = openapi_route

        self.docs_theme = DEFAULT_API_THEME
        self.docs_route = docs_route

        self.plugins = [MarshmallowPlugin()] if plugins is None else plugins

        if self.openapi_version is not None:
            self.app.add_route(self.openapi_route, self.schema_response)

        if self.docs_route is not None:
            self.app.add_route(self.docs_route, self.docs_response)

        theme_path = (
            Path(apistar.__file__).parent / "themes" / self.docs_theme / "static"
        ).resolve()

        self.static_route = static_route

        self.app.static_app.add_directory(theme_path)

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
                operations = yaml_utils.load_operations_from_docstring(
                    route.description
                )
                spec.path(path=route.route, operations=operations)

        for name, schema in self.schemas.items():
            spec.components.schema(name, schema=schema)

        return spec

    @property
    def openapi(self):
        return self._apispec.to_yaml()

    def add_schema(self, name, schema, check_existing=True):
        """Adds a marshmallow schema to the API specification."""
        if check_existing:
            assert name not in self.schemas

        self.schemas[name] = schema

    def schema(self, name, **options):
        """Decorator for creating new routes around function and class definitions.

        Usage::

            from marshmallow import Schema, fields

            @api.schema("Pet")
            class PetSchema(Schema):
                name = fields.Str()

        """

        def decorator(f):
            self.add_schema(name=name, schema=f, **options)
            return f

        return decorator

    @property
    def docs(self):

        loader = jinja2.PrefixLoader(
            {
                self.docs_theme: jinja2.PackageLoader(
                    "apistar", os.path.join("themes", self.docs_theme, "templates")
                )
            }
        )
        env = jinja2.Environment(autoescape=True, loader=loader)
        document = apistar.document.Document()
        document.content = yaml.safe_load(self.openapi)

        template = env.get_template("/".join([self.docs_theme, "index.html"]))

        return template.render(
            document=document,
            langs=["javascript", "python"],
            code_style=None,
            static_url=self.static_url,
            schema_url="/schema.yml",
        )

    def static_url(self, asset):
        """Given a static asset, return its URL path."""
        assert self.static_route is not None
        return f"{self.static_route}/{str(asset)}"

    def docs_response(self, req, resp):
        resp.html = self.docs

    def schema_response(self, req, resp):
        resp.status_code = status_codes.HTTP_200
        resp.headers["Content-Type"] = "application/x-yaml"
        resp.content = self.openapi
