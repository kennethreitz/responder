import importlib.util

import pytest
import yaml
from openapi_spec_validator import validate
from pydantic import BaseModel

import responder
from responder.ext.auth import BearerAuth


def _load_module(path):
    spec = importlib.util.spec_from_file_location("contract_client", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ItemIn(BaseModel):
    name: str


class ItemOut(BaseModel):
    id: int
    name: str


def _contract_api():
    def verify(token):
        users = {"writer-token": {"name": "writer", "scopes": ["items:write"]}}
        return users.get(token)

    api = responder.API(
        title="Contract Service",
        version="1",
        openapi="3.1.0",
        secret_key="x" * 32,
        allowed_hosts=[";"],
        session_https_only=False,
    )
    writer = api.policy("writer", BearerAuth(verify=verify).requires("items:write"))

    @api.get(
        "/items/{id:int}",
        operation_id="get_item",
        tags=["items"],
        response_model=ItemOut,
        responses={404: "Item not found"},
        examples={
            "found": {
                "summary": "Existing item",
                "value": {"id": 7, "name": "tea"},
            }
        },
    )
    def get_item(req, resp, *, id: int):
        resp.media = {"id": id, "name": "tea"}

    @api.post(
        "/items",
        operation_id="create_item",
        tags=["items"],
        auth=writer,
        response_model=ItemOut,
        responses={
            201: {
                "description": "Created",
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/ItemOut"}
                    }
                },
            }
        },
        response_examples={
            201: {
                "created": {
                    "summary": "Created item",
                    "value": {"id": 1, "name": "coffee"},
                }
            }
        },
    )
    def create_item(req, resp, *, item: ItemIn, user):
        resp.created({"id": 1, "name": item.name}, location="/items/1")

    @api.delete(
        "/items/{id:int}",
        operation_id="delete_item",
        tags=["items"],
        auth=writer,
        responses={204: "Deleted"},
    )
    def delete_item(req, resp, *, id: int, user):
        resp.no_content(headers={"X-Deleted": str(id)})

    @api.get(
        "/conflict",
        operation_id="conflict",
        tags=["diagnostics"],
        responses={409: "Conflict"},
    )
    def conflict(req, resp):
        resp.problem(
            409,
            "Contract conflict",
            type="https://example.com/problems/contract-conflict",
        )

    return api


def test_contract_app_openapi_and_generated_python_client(tmp_path):
    api = _contract_api()
    spec = yaml.safe_load(api.requests.get("/schema.yml").content)

    validate(spec)
    assert api.auth_policies["writer"].name == "writer"
    assert spec["paths"]["/items"]["post"]["security"] == [
        {"bearerAuth": ["items:write"]}
    ]
    assert (
        spec["paths"]["/items"]["post"]["responses"]["201"]["content"][
            "application/json"
        ]["examples"]["created"]["value"]["name"]
        == "coffee"
    )

    path = tmp_path / "contract_client.py"
    api.generate_client(path, class_name="ContractClient")
    module = _load_module(path)
    client = module.ContractClient(session=api.requests, bearer_token="writer-token")

    assert client.get_item(7) == {"id": 7, "name": "tea"}
    assert client.create_item(body={"name": "coffee"}) == {
        "id": 1,
        "name": "coffee",
    }
    assert client.delete_item(7) is None

    with pytest.raises(module.APIError) as excinfo:
        client.conflict()

    assert excinfo.value.status_code == 409
    assert excinfo.value.problem["type"] == (
        "https://example.com/problems/contract-conflict"
    )
