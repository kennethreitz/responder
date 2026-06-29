"""v5.3: list/union response_model validation + OpenAPI schema."""

from typing import Union

import yaml
from pydantic import BaseModel
from starlette.testclient import TestClient

import responder


class Item(BaseModel):
    id: int
    name: str


class Err(BaseModel):
    detail: str


def _api():
    return responder.API(
        title="T", version="1", openapi="3.0.2", secret_key="x" * 32,
        allowed_hosts=[";"], session_https_only=False,
    )


def _client(api):
    return TestClient(api, base_url="http://;")


def _all_refs(obj):
    refs = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                refs.append(value) if key == "$ref" else walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return refs


def test_list_response_model_coerces_and_strips():
    api = _api()

    @api.get("/items", response_model=list[Item])
    def items(req, resp):
        resp.media = [{"id": "1", "name": "a", "extra": "drop"}, {"id": 2, "name": "b"}]

    assert _client(api).get("/items").json() == [
        {"id": 1, "name": "a"},
        {"id": 2, "name": "b"},
    ]


def test_list_response_model_fails_closed_on_bad_data():
    api = _api()

    @api.get("/items", response_model=list[Item])
    def items(req, resp):
        resp.media = [{"id": "notanint", "name": "a"}]

    assert _client(api).get("/items").status_code == 500


def test_list_response_model_openapi_is_array_with_refs():
    api = _api()

    @api.get("/items", response_model=list[Item])
    def items(req, resp):
        resp.media = []

    spec = yaml.safe_load(_client(api).get("/schema.yml").content)
    schema = spec["paths"]["/items"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert schema["type"] == "array"
    assert schema["items"]["$ref"].endswith("/Item")
    # No garbage "list" component; every ref resolves.
    assert "list" not in spec["components"]["schemas"]
    assert "Item" in spec["components"]["schemas"]
    for ref in _all_refs(spec):
        assert ref.split("/")[-1] in spec["components"]["schemas"]


def test_union_response_model_openapi():
    api = _api()

    @api.get("/u", response_model=Union[Item, Err])
    def u(req, resp):
        resp.media = {"id": 1, "name": "a"}

    spec = yaml.safe_load(_client(api).get("/schema.yml").content)
    schema = spec["paths"]["/u"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert "anyOf" in schema or "oneOf" in schema
    assert {"Item", "Err"} <= set(spec["components"]["schemas"])


def test_generic_return_annotation_stays_no_op():
    # Implicit (return-annotation) generics are intentionally NOT validated, so
    # an app returning loose data keeps working.
    api = _api()

    @api.get("/items")
    def items(req, resp) -> list[Item]:
        resp.media = [{"id": "notanint", "name": "a"}]  # would fail if validated

    assert _client(api).get("/items").status_code == 200
