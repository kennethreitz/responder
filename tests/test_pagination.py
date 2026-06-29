"""v6.2: pagination helpers (Page + paginate)."""

import re

import yaml
from pydantic import BaseModel
from starlette.testclient import TestClient

import responder
from responder import Query
from responder.ext.pagination import Page, paginate


class Item(BaseModel):
    id: int
    name: str


def _client(api):
    return TestClient(api, base_url="http://;")


def test_paginate_slices_collection():
    p = paginate(list(range(25)), page=2, size=10)
    assert p.page == 2
    assert p.size == 10
    assert p.total == 25
    assert p.pages == 3
    assert p.items == list(range(10, 20))


def test_paginate_with_explicit_total_does_not_reslice():
    p = paginate([1, 2, 3], page=2, size=3, total=10)
    assert p.items == [1, 2, 3]  # already the page slice
    assert p.total == 10
    assert p.pages == 4


def test_paginate_clamps_page_and_size():
    p = paginate(list(range(5)), page=0, size=0)
    assert p.page == 1
    assert p.size == 1


def test_paginate_empty():
    p = paginate([], page=1, size=10)
    assert p.items == []
    assert p.total == 0
    assert p.pages == 0


def test_page_response_model_runtime():
    api = responder.API(
        title="T", version="1", openapi="3.0.2", secret_key="x" * 16,
        allowed_hosts=[";"], session_https_only=False,
    )

    @api.get("/items", response_model=Page[Item])
    def items(req, resp, *, page: int = Query(1, ge=1), size: int = Query(10, ge=1, le=100)):
        data = [{"id": i, "name": f"n{i}", "extra": "drop"} for i in range(25)]
        resp.media = paginate(data, page=page, size=size)

    r = _client(api).get("/items?page=2&size=10")
    body = r.json()
    assert body["page"] == 2
    assert body["total"] == 25
    assert body["pages"] == 3
    assert len(body["items"]) == 10
    assert body["items"][0] == {"id": 10, "name": "n10"}  # extra field stripped


def test_page_openapi_is_valid_and_inlined():
    api = responder.API(
        title="T", version="1", openapi="3.0.2", secret_key="x" * 16,
        allowed_hosts=[";"], session_https_only=False,
    )

    @api.get("/items", response_model=Page[Item])
    def items(req, resp):
        resp.media = paginate([], page=1, size=10)

    spec = yaml.safe_load(_client(api).get("/schema.yml").content)
    schemas = spec["components"]["schemas"]
    # Only the element model is a named component; Page[Item] is inlined.
    assert "Item" in schemas
    assert all(re.fullmatch(r"[a-zA-Z0-9._-]+", k) for k in schemas)  # valid keys
    schema = spec["paths"]["/items"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert schema["type"] == "object"
    assert schema["properties"]["items"]["items"]["$ref"].endswith("/Item")
