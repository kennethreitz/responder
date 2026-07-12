"""Responder 9.1 typed return contracts and response semantics."""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Annotated

import pytest
import yaml
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

import responder
from responder.ext.pagination import Page, paginate


class Item(BaseModel):
    id: int
    name: str


class Error(BaseModel):
    detail: str


class Accepted(BaseModel):
    job_id: int


class ItemDict(TypedDict):
    id: int
    name: str


@dataclass
class ItemData:
    id: int
    name: str


def make_api(*, openapi: str | None = "3.1.0", **kwargs) -> responder.API:
    return responder.API(
        title="Contracts",
        version="1",
        openapi=openapi,
        allowed_hosts=[";"],
        sessions=False,
        **kwargs,
    )


def schema_for(api: responder.API, path: str, method: str = "get") -> dict:
    spec = yaml.safe_load(api.requests.get("/schema.yml").content)
    return spec["paths"][path][method]["responses"]


@pytest.mark.parametrize("openapi", ["3.0.2", "3.1.0"])
def test_generic_annotation_drives_runtime_and_openapi(openapi):
    api = make_api(openapi=openapi)

    @api.get("/items")
    def items(req, resp) -> list[Item]:
        return [{"id": "1", "name": "tea", "secret": "drop"}]

    assert api.requests.get("/items").json() == [{"id": 1, "name": "tea"}]
    response = schema_for(api, "/items")["200"]
    schema = response["content"]["application/json"]["schema"]
    assert schema["type"] == "array"
    assert schema["items"]["$ref"].endswith("/Item")


def test_page_union_dict_and_dataclass_annotations_are_contracts():
    api = make_api()

    @api.get("/page")
    def page(req, resp) -> Page[Item]:
        return paginate([{"id": "1", "name": "tea"}], page=1, size=20)

    @api.get("/choice")
    def choice(req, resp) -> Item | Error:
        return {"detail": "not selected"}

    @api.get("/mapping")
    def mapping(req, resp) -> ItemDict:
        return {"id": 2, "name": "coffee", "extra": "drop"}

    @api.get("/dataclass")
    def dataclass_item(req, resp) -> ItemData:
        return ItemData(id=3, name="water")

    assert api.requests.get("/page").json()["items"] == [{"id": 1, "name": "tea"}]
    assert api.requests.get("/choice").json() == {"detail": "not selected"}
    assert api.requests.get("/mapping").json() == {"id": 2, "name": "coffee"}
    assert api.requests.get("/dataclass").json() == {"id": 3, "name": "water"}

    page_schema = schema_for(api, "/page")["200"]["content"]["application/json"]["schema"]
    choice_schema = schema_for(api, "/choice")["200"]["content"]["application/json"][
        "schema"
    ]
    assert page_schema["type"] == "object"
    assert "anyOf" in choice_schema or "oneOf" in choice_schema


def test_annotated_constraints_and_wrapped_handlers_are_preserved():
    api = make_api()

    def preserve_signature(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            return view(*args, **kwargs)

        return wrapped

    @api.get("/positive")
    @preserve_signature
    def positive(req, resp) -> Annotated[int, Field(gt=0)]:
        return 0

    response = api.requests.get("/positive")
    assert response.status_code == 500
    schema = schema_for(api, "/positive")["200"]["content"]["application/json"]["schema"]
    assert schema["exclusiveMinimum"] == 0


@pytest.mark.parametrize(
    ("path", "annotation", "value", "media_type", "expected", "schema_type"),
    [
        ("/integer", int, "7", "application/json", 7, "integer"),
        ("/boolean", bool, True, "application/json", True, "boolean"),
        ("/text", str, "hello", "text/plain", "hello", "string"),
        (
            "/bytes",
            bytes,
            b"hello",
            "application/octet-stream",
            b"hello",
            "string",
        ),
    ],
)
def test_scalar_annotations_drive_body_and_content_type(
    path, annotation, value, media_type, expected, schema_type
):
    api = make_api()

    def scalar(req, resp):
        return value

    scalar.__annotations__["return"] = annotation
    api.get(path)(scalar)

    response = api.requests.get(path)
    actual = response.json() if media_type == "application/json" else response.content
    if media_type == "text/plain":
        actual = response.text
    assert actual == expected
    assert response.headers["content-type"].startswith(media_type)

    declared = schema_for(api, path)["200"]["content"]
    assert list(declared) == [media_type]
    assert declared[media_type]["schema"]["type"] == schema_type


def test_response_model_false_disables_annotation_runtime_and_schema():
    api = make_api()

    @api.get("/item", response_model=False)
    def item(req, resp) -> Item:
        return {"id": "not-an-int", "name": "tea", "extra": True}

    response = api.requests.get("/item")
    assert response.status_code == 200
    assert response.json() == {
        "id": "not-an-int",
        "name": "tea",
        "extra": True,
    }
    assert "content" not in schema_for(api, "/item")["200"]


def test_explicit_scalar_response_model_remains_json():
    api = make_api()

    @api.get("/message", response_model=str)
    def message(req, resp):
        return "hello"

    response = api.requests.get("/message")
    assert response.json() == "hello"
    assert response.headers["content-type"].startswith("application/json")
    assert list(schema_for(api, "/message")["200"]["content"]) == ["application/json"]


def test_missing_or_invalid_annotated_response_fails_closed():
    api = make_api()

    @api.get("/missing")
    def missing(req, resp) -> Item:
        return None

    @api.get("/invalid")
    def invalid(req, resp) -> Item:
        return {"id": "nope", "name": "tea"}

    for path in ("/missing", "/invalid"):
        response = api.requests.get(path)
        assert response.status_code == 500
        assert response.headers["content-type"].startswith("application/problem+json")


def test_error_response_skips_success_contract_validation():
    api = make_api()

    @api.get("/item")
    def item(req, resp) -> Item:
        resp.problem(404, "gone")

    response = api.requests.get("/item")
    assert response.status_code == 404
    assert response.json()["detail"] == "gone"


def test_status_response_model_drives_runtime_and_openapi():
    api = make_api()

    @api.get("/items/{item_id}", responses={404: Error})
    def item(req, resp, *, item_id: str) -> Item:
        return {"detail": "gone", "private": True}, 404

    response = api.requests.get("/items/1")
    assert response.status_code == 404
    assert response.json() == {"detail": "gone"}

    declared = schema_for(api, "/items/{item_id}")["404"]
    assert declared["description"] == "Not Found"
    assert declared["content"]["application/json"]["schema"]["$ref"].endswith("/Error")


def test_status_response_model_supports_metadata_and_mutation_style():
    api = make_api()

    @api.get(
        "/items/{item_id}",
        responses={
            404: {
                "model": Error,
                "description": "The item does not exist",
                "headers": {"X-Reason": {"schema": {"type": "string"}}},
            }
        },
        response_examples={404: {"detail": "gone"}},
    )
    def item(req, resp, *, item_id: str) -> Item:
        resp.status_code = 404
        resp.headers["X-Reason"] = "deleted"
        resp.media = {"detail": "gone", "private": True}

    response = api.requests.get("/items/1")
    assert response.status_code == 404
    assert response.json() == {"detail": "gone"}

    declared = schema_for(api, "/items/{item_id}")["404"]
    assert declared["description"] == "The item does not exist"
    assert "model" not in declared
    assert declared["headers"]["X-Reason"]["schema"]["type"] == "string"
    assert declared["content"]["application/json"]["examples"]["default"] == {
        "value": {"detail": "gone"}
    }


def test_status_response_model_validation_fails_closed():
    api = make_api()

    @api.get("/items/{item_id}", responses={404: Error})
    def item(req, resp, *, item_id: str) -> Item:
        return {"message": "wrong shape"}, 404

    response = api.requests.get("/items/1")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")


def test_alternate_success_status_uses_its_own_contract():
    api = make_api()

    @api.post("/items", responses={202: Accepted})
    def create(req, resp) -> Item:
        return {"job_id": "7", "extra": "drop"}, 202

    response = api.requests.post("/items")
    assert response.status_code == 202
    assert response.json() == {"job_id": 7}

    responses = schema_for(api, "/items", "post")
    assert responses["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/Item"
    )
    assert responses["202"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/Accepted"
    )


def test_typed_422_response_takes_precedence_over_automatic_schema():
    api = make_api()

    @api.post("/items", responses={422: Error})
    def create(req, resp, *, item: Item) -> Item:
        return {"detail": "duplicate"}, 422

    response = api.requests.post("/items", json={"id": 1, "name": "tea"})
    assert response.status_code == 422
    assert response.json() == {"detail": "duplicate"}
    schema = schema_for(api, "/items", "post")["422"]["content"]["application/json"][
        "schema"
    ]
    assert schema["$ref"].endswith("/Error")


def test_typed_response_rejects_body_forbidden_status():
    api = make_api()

    with pytest.raises(ValueError, match="cannot declare a response model"):

        @api.get("/", responses={204: Item})
        def item(req, resp):
            return None


def test_unresolved_return_annotation_logs_registration_diagnostic(caplog):
    api = make_api(openapi=None)

    with caplog.at_level("WARNING", logger="responder"):

        def unresolved(req, resp):
            return {"ok": True}

        unresolved.__annotations__["return"] = "MissingResponseModel"
        api.get("/")(unresolved)

    assert "MissingResponseModel" in caplog.text
    assert "response_model=False" in caplog.text


def test_status_contract_is_frozen_per_shared_handler_registration():
    api = make_api(openapi=None)

    def shared(req, resp):
        return {"detail": "gone", "private": True}, 404

    api.get("/typed", responses={404: Error})(shared)
    api.get("/plain")(shared)

    assert api.requests.get("/typed").json() == {"detail": "gone"}
    assert api.requests.get("/plain").json() == {
        "detail": "gone",
        "private": True,
    }


def test_after_hook_output_is_covered_by_the_contract():
    api = make_api()

    def corrupt(req, resp):
        resp.media = {"id": "not-an-int", "name": "tea"}

    @api.get("/item", after=corrupt)
    def item(req, resp) -> Item:
        return Item(id=1, name="tea")

    assert api.requests.get("/item").status_code == 500


def test_class_based_view_method_annotation_is_inferred():
    api = make_api()

    @api.route("/items", methods=["GET", "POST"])
    class Items:
        def on_get(self, req, resp) -> list[Item]:
            return [{"id": "1", "name": "tea"}]

        def on_post(self, req, resp) -> Item:
            return {"id": "2", "name": "coffee", "extra": "drop"}

    assert api.requests.get("/items").json() == [{"id": 1, "name": "tea"}]
    assert api.requests.post("/items").json() == {"id": 2, "name": "coffee"}
    responses = yaml.safe_load(api.requests.get("/schema.yml").content)["paths"]["/items"]
    assert (
        responses["get"]["responses"]["200"]["content"]["application/json"]["schema"][
            "type"
        ]
        == "array"
    )
    assert responses["post"]["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/Item")


def test_flask_tuple_annotation_infers_its_body_contract():
    api = make_api()

    @api.post("/items", status_code=201)
    def create(req, resp) -> tuple[Item, int, dict[str, str]]:
        return {"id": "1", "name": "tea", "extra": "drop"}, 201, {"Location": "/items/1"}

    response = api.requests.post("/items")
    assert response.status_code == 201
    assert response.headers["location"] == "/items/1"
    assert response.json() == {"id": 1, "name": "tea"}
    schema = schema_for(api, "/items", "post")["201"]["content"]["application/json"][
        "schema"
    ]
    assert schema["$ref"].endswith("/Item")


@pytest.mark.parametrize(
    "result",
    [
        ({"ok": True},),
        ({"ok": True}, 99),
        ({"ok": True}, 600),
        ({"ok": True}, True),
        ({"ok": True}, 200, []),
        ({"ok": True}, 200, {}, "extra"),
    ],
)
def test_malformed_flask_tuple_returns_500(result):
    api = make_api(openapi=None)

    @api.get("/")
    def broken(req, resp):
        return result

    client = api.test_client(raise_server_exceptions=False)
    assert client.get("/").status_code == 500


@pytest.mark.parametrize("status", [101, 204, 205, 304])
def test_body_forbidden_statuses_never_send_a_body(status):
    api = make_api()

    @api.get("/", status_code=status)
    def body(req, resp) -> dict[str, bool]:
        return {"sent": True}

    response = api.requests.get("/")
    assert response.status_code == status
    assert response.content == b""
    assert "content-type" not in response.headers
    assert "content" not in schema_for(api, "/")[str(status)]


def test_head_validates_get_contract_but_sends_no_body():
    api = make_api(openapi=None)

    @api.get("/")
    def body(req, resp) -> list[Item]:
        return [{"id": "1", "name": "tea"}]

    response = api.requests.head("/")
    assert response.status_code == 200
    assert response.content == b""
    assert int(response.headers["content-length"]) > 0
