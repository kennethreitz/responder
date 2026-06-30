"""Generated OpenAPI documents validate as real OpenAPI specs."""

import pytest
import yaml
from openapi_spec_validator import validate
from pydantic import BaseModel
from starlette.testclient import TestClient

import responder
from responder.ext.auth import BearerAuth


def _api(**kwargs):
    return responder.API(
        title="T",
        version="1",
        secret_key="x" * 32,
        allowed_hosts=[";"],
        session_https_only=False,
        **kwargs,
    )


def _schema(api):
    response = TestClient(api, base_url="http://;").get("/schema.yml")
    assert response.status_code == 200
    return yaml.safe_load(response.content)


@pytest.mark.parametrize("openapi_version", ["3.0.2", "3.1.0"])
@pytest.mark.parametrize("problem_details", [True, False])
def test_generated_openapi_spec_validates(openapi_version, problem_details):
    class ItemIn(BaseModel):
        name: str

    class ItemOut(BaseModel):
        id: int
        name: str

    auth = BearerAuth(tokens=["secret"]).requires("items:write")
    api = _api(
        openapi=openapi_version,
        auth=auth.optional(),
        problem_details=problem_details,
        request_timeout=1,
    )

    @api.get("/items/{id}", response_model=ItemOut)
    def read_item(req, resp, *, id: int, q: str | None = responder.Query(None)):
        resp.media = {"id": id, "name": q or "item"}

    @api.post("/items", response_model=ItemOut)
    def create_item(req, resp, *, item: ItemIn, user):
        resp.media = {"id": 1, "name": item.name}

    validate(_schema(api))
