"""v5: type-hint-driven OpenAPI generation."""

import yaml

import responder
from responder import Path, Query


def _api():
    return responder.API(
        title="T", version="1", openapi="3.0.2",
        allowed_hosts=[";"], session_https_only=False,
    )


def _spec(api):
    return yaml.safe_load(api.requests.get("http://;/schema.yml").content)


def test_route_without_docstring_appears(needs_openapi):
    api = _api()

    @api.route("/ping")
    def ping(req, resp):
        resp.text = "pong"

    spec = _spec(api)
    assert "/ping" in spec["paths"]
    assert "get" in spec["paths"]["/ping"]


def test_internal_routes_excluded(needs_openapi):
    api = _api()

    @api.route("/shown")
    def shown(req, resp):
        resp.text = "y"

    spec = _spec(api)
    assert "/shown" in spec["paths"]
    assert "/schema.yml" not in spec["paths"]


def test_typed_route_generates_params_schema_and_422(needs_openapi):
    from pydantic import BaseModel

    class ItemOut(BaseModel):
        id: int
        name: str

    api = _api()

    @api.route("/items")
    def items(req, resp, *, q: str = Query(...)) -> ItemOut:
        resp.media = {"id": 1, "name": "x"}

    spec = _spec(api)
    item = spec["paths"]["/items"]
    op = item["get"]
    params = item.get("parameters", []) + op.get("parameters", [])
    assert any(p["name"] == "q" and p["in"] == "query" for p in params)
    assert "ItemOut" in str(op["responses"]["200"])
    assert "422" in op["responses"]
    assert "ItemOut" in spec["components"]["schemas"]


def test_plain_path_annotation_generates_typed_schema(needs_openapi):
    api = _api()

    @api.route("/users/{user_id}")
    def user(req, resp, *, user_id: int):
        resp.media = {"user_id": user_id}

    spec = _spec(api)
    params = spec["paths"]["/users/{user_id}"]["get"]["parameters"]
    assert params == [
        {
            "name": "user_id",
            "in": "path",
            "required": True,
            "schema": {"type": "integer"},
        }
    ]


def test_path_marker_alias_and_metadata_appear_in_openapi(needs_openapi):
    api = _api()

    @api.route("/users/{uid}")
    def user(
        req,
        resp,
        *,
        user_id: int = Path(..., alias="uid", ge=1, description="User ID"),
    ):
        resp.media = {"user_id": user_id}

    spec = _spec(api)
    params = spec["paths"]["/users/{uid}"]["get"]["parameters"]
    assert params == [
        {
            "name": "uid",
            "in": "path",
            "required": True,
            "description": "User ID",
            "schema": {"type": "integer", "minimum": 1},
        }
    ]


def test_uuid_convertor_generates_uuid_schema(needs_openapi):
    api = _api()

    @api.route("/users/{user_id:uuid}")
    def user(req, resp, *, user_id):
        resp.media = {"user_id": user_id}

    spec = _spec(api)
    params = spec["paths"]["/users/{user_id}"]["get"]["parameters"]
    assert params == [
        {
            "name": "user_id",
            "in": "path",
            "required": True,
            "schema": {"type": "string", "format": "uuid"},
        }
    ]


def test_request_model_generates_request_body(needs_openapi):
    from pydantic import BaseModel

    class ItemIn(BaseModel):
        name: str

    api = _api()

    @api.route("/create", methods=["POST"], request_model=ItemIn)
    async def create(req, resp):
        resp.media = {"ok": True}

    spec = _spec(api)
    op = spec["paths"]["/create"]["post"]
    assert "requestBody" in op
    assert "ItemIn" in str(op["requestBody"])


def test_include_in_schema_false_excludes_route(needs_openapi):
    api = _api()

    @api.route("/hidden", include_in_schema=False)
    def hidden(req, resp):
        resp.text = "x"

    @api.route("/visible")
    def visible(req, resp):
        resp.text = "y"

    spec = _spec(api)
    assert "/visible" in spec["paths"]
    assert "/hidden" not in spec["paths"]


def _all_refs(obj):
    refs = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref":
                    refs.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return refs


def test_nested_models_are_hoisted_and_refs_resolve(needs_openapi):
    from typing import Optional

    from pydantic import BaseModel

    class Tag(BaseModel):
        label: str

    class Pet(BaseModel):
        name: str
        tag: Optional[Tag] = None

    api = _api()  # default openapi="3.0.2"

    @api.route("/pets", methods=["POST"], request_model=Pet, response_model=Pet)
    async def create(req, resp):
        resp.media = {"name": "rex"}

    spec = _spec(api)
    schemas = spec["components"]["schemas"]
    # The nested model is hoisted to its own top-level component.
    assert "Pet" in schemas
    assert "Tag" in schemas
    # Every $ref in the document resolves to a registered component.
    for ref in _all_refs(spec):
        assert ref.startswith("#/components/schemas/")
        assert ref.split("/")[-1] in schemas


def test_optional_field_downconverts_to_nullable_under_30(needs_openapi):
    from typing import Optional

    from pydantic import BaseModel

    class Item(BaseModel):
        name: str
        note: Optional[str] = None

    api = _api()  # 3.0.2

    @api.route("/items", methods=["POST"], request_model=Item)
    async def create(req, resp):
        resp.media = {"ok": True}

    spec = _spec(api)
    note = spec["components"]["schemas"]["Item"]["properties"]["note"]
    # 3.0 cannot express {"type": "null"}; it must become nullable.
    assert note.get("nullable") is True
    assert note.get("type") == "string"
    assert "anyOf" not in note


def test_optional_field_keeps_anyof_null_under_31(needs_openapi):
    from typing import Optional

    from pydantic import BaseModel

    class Item(BaseModel):
        name: str
        note: Optional[str] = None

    api = responder.API(
        title="T", version="1", openapi="3.1.0",
        allowed_hosts=[";"], session_https_only=False,
    )

    @api.route("/items", methods=["POST"], request_model=Item)
    async def create(req, resp):
        resp.media = {"ok": True}

    spec = _spec(api)
    note = spec["components"]["schemas"]["Item"]["properties"]["note"]
    # 3.1 is a JSON-Schema superset, so the null-union is left intact.
    assert {"type": "null"} in note["anyOf"]


def test_docstring_still_overrides(needs_openapi):
    api = _api()

    @api.route("/described")
    def described(req, resp):
        """An endpoint.
        ---
        get:
            summary: Custom summary
            responses:
                200:
                    description: custom desc
        """
        resp.text = "x"

    spec = _spec(api)
    op = spec["paths"]["/described"]["get"]
    assert op.get("summary") == "Custom summary"
