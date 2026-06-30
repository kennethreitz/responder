import yaml
from openapi_spec_validator import validate

from examples.todo import TodoStore, create_api

AUTH = {"Authorization": "Bearer demo-token"}


def test_todo_example_openapi_contract():
    api = create_api(store=TodoStore.seeded())
    spec = yaml.safe_load(api.requests.get("/schema.yml").content)

    validate(spec)
    assert sorted(api.auth_policies) == ["writer"]

    paths = spec["paths"]
    assert paths["/todos"]["get"]["operationId"] == "list_todos"
    assert paths["/todos"]["get"]["parameters"][0]["name"] == "completed"
    assert paths["/todos"]["post"]["security"] == [
        {"bearerAuth": ["todos:write"]}
    ]
    assert paths["/todos"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/TodoCreate"}
    assert paths["/todos/{todo_id}"]["patch"]["requestBody"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/TodoPatch"}


def test_todo_example_walks_the_resource_lifecycle():
    api = create_api(store=TodoStore.seeded())

    listed = api.requests.get("/todos").json()
    assert [todo["title"] for todo in listed] == [
        "Sketch the release post",
        "Review generated client ergonomics",
    ]

    unauthorized = api.requests.post("/todos", json={"title": "Nope"})
    assert unauthorized.status_code == 401

    created = api.requests.post(
        "/todos",
        headers=AUTH,
        json={
            "title": "Draft the tutorial",
            "notes": "Show the smallest useful app.",
            "priority": "high",
            "tags": ["Docs", " docs ", "Examples"],
        },
    )
    assert created.status_code == 201
    assert created.headers["location"] == "/todos/3"
    assert created.json()["owner"] == "Ada"
    assert created.json()["tags"] == ["docs", "examples"]

    filtered = api.requests.get("/todos?tag=docs").json()
    assert [todo["id"] for todo in filtered] == [3]

    patched = api.requests.patch(
        "/todos/3",
        headers=AUTH,
        json={"due": "2026-07-01"},
    )
    body = patched.json()
    assert body["due"] == "2026-07-01"

    completed = api.requests.post("/todos/3/complete", headers=AUTH).json()
    assert completed["completed"] is True
    assert completed["completed_at"] is not None

    active = api.requests.get("/todos?completed=false").json()
    assert [todo["id"] for todo in active] == [1]

    deleted = api.requests.delete("/todos/3", headers=AUTH)
    assert deleted.status_code == 204
    assert deleted.headers["x-deleted-todo"] == "3"
    assert deleted.content == b""

    missing = api.requests.get("/todos/3")
    assert missing.status_code == 404
    assert missing.json()["todo_id"] == 3
