import importlib.util

import pytest
import yaml
from openapi_spec_validator import validate

from examples.atelier import create_api


def _load_module(path):
    spec = importlib.util.spec_from_file_location("atelier_client", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_atelier_example_is_the_golden_contract_app(tmp_path):
    api = create_api()
    spec = yaml.safe_load(api.requests.get("/schema.yml").content)

    validate(spec)
    assert sorted(api.auth_policies) == ["publisher", "viewer", "writer"]

    paths = spec["paths"]
    assert paths["/projects"]["get"]["security"] == [{}, {"bearerAuth": []}]
    assert paths["/projects"]["post"]["security"] == [
        {"bearerAuth": ["projects:write"]}
    ]
    assert paths["/projects"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/ProjectIn"}
    assert paths["/projects/{project_id}/publish"]["post"]["security"] == [
        {"bearerAuth": ["projects:publish"]}
    ]
    assert (
        paths["/projects"]["post"]["responses"]["201"]["content"][
            "application/json"
        ]["examples"]["created"]["value"]["title"]
        == "Night Market"
    )
    assert paths["/projects"]["get"]["x-codeSamples"][0]["lang"] == "curl"

    client_path = tmp_path / "atelier_client.py"
    api.generate_client(client_path, class_name="AtelierClient")
    module = _load_module(client_path)
    client = module.AtelierClient(
        session=api.requests,
        bearer_token="curator-token",
        validate=True,
    )

    assert [project["title"] for project in client.list_projects()] == [
        "Field Notes",
        "Signal Room",
    ]

    created = client.create_project(
        body={
            "title": "Night Market",
            "summary": "A glowing launch plan for the evening.",
            "mood": "bright",
        }
    )
    assert created["id"] == 3
    assert created["owner"] == "Ada"

    published = client.publish_project(3)
    assert published["status"] == "published"

    assert client.delete_project(3) is None

    with pytest.raises(module.APIError) as excinfo:
        client.get_project(404)

    problem = excinfo.value.problem
    assert problem["type"] == "https://atelier.example/problems/project-not-found"
    assert problem["project_id"] == 404
