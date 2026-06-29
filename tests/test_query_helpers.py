"""v6.3: sorting & filtering helpers (responder.ext.query)."""

import pytest
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.testclient import TestClient

import responder
from responder import Query
from responder.ext.pagination import Page, paginate
from responder.ext.query import filter_items, parse_sort, sort_items

DATA = [
    {"name": "banana", "qty": 3, "status": "active"},
    {"name": "apple", "qty": 10, "status": "inactive"},
    {"name": "cherry", "qty": 3, "status": "active"},
]


def test_parse_sort():
    assert parse_sort("name,-qty") == [("name", False), ("qty", True)]
    assert parse_sort("") == []
    assert parse_sort(None) == []


def test_sort_single_and_multi_key():
    assert [x["name"] for x in sort_items(DATA, "name")] == ["apple", "banana", "cherry"]
    # qty desc, then name asc within ties
    assert [(x["qty"], x["name"]) for x in sort_items(DATA, "-qty,name")] == [
        (10, "apple"),
        (3, "banana"),
        (3, "cherry"),
    ]


def test_sort_allowlist_blocks_arbitrary_fields():
    with pytest.raises(HTTPException) as exc:
        sort_items(DATA, "secret", allowed={"name"})
    assert exc.value.status_code == 400


def test_sort_none_values_last():
    rows = [{"v": "a"}, {"v": None}, {"v": "b"}]
    assert [x["v"] for x in sort_items(rows, "v")] == ["a", "b", None]


def test_sort_incomparable_raises_400():
    rows = [{"v": 1}, {"v": "x"}]
    with pytest.raises(HTTPException) as exc:
        sort_items(rows, "v")
    assert exc.value.status_code == 400


def test_filter_equality_and_none_skip():
    assert [x["name"] for x in filter_items(DATA, {"status": "active"})] == [
        "banana",
        "cherry",
    ]
    assert filter_items(DATA, {"status": None}) == DATA  # None skipped
    assert [x["name"] for x in filter_items(DATA, {"status": "active", "qty": 3})] == [
        "banana",
        "cherry",
    ]


def test_helpers_work_on_objects():
    class Row:
        def __init__(self, n):
            self.n = n

    rows = [Row(3), Row(1), Row(2)]
    assert [r.n for r in sort_items(rows, "n")] == [1, 2, 3]


def test_full_list_endpoint_flow():
    class Item(BaseModel):
        name: str
        qty: int

    api = responder.API(
        title="T", version="1", openapi="3.0.2", secret_key="x" * 16,
        allowed_hosts=[";"], session_https_only=False,
    )

    @api.get("/items", response_model=Page[Item])
    def items(
        req, resp, *,
        status: str = Query(None),
        sort: str = Query("name"),
        page: int = Query(1, ge=1),
        size: int = Query(20, ge=1, le=100),
    ):
        rows = filter_items(DATA, {"status": status})
        rows = sort_items(rows, sort, allowed={"name", "qty"})
        resp.media = paginate(rows, page=page, size=size)

    client = TestClient(api, base_url="http://;")
    body = client.get("/items?status=active&sort=-qty&size=1&page=1").json()
    assert body["total"] == 2  # two active
    assert body["pages"] == 2
    assert body["items"] == [{"name": "banana", "qty": 3}]  # qty desc, first page

    # disallowed sort field -> 400
    assert client.get("/items?sort=status").status_code == 400
