"""v5: type-hint-driven OpenAPI generation."""

import yaml

import responder
from responder import Query


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
