import importlib

import yaml
from openapi_spec_validator import validate
from starlette.testclient import TestClient

from examples import (
    helloworld,
    lifespan,
    marimo_mount,
    rest_api,
    sse_stream,
    user,
    websocket_chat,
)


def _openapi(api):
    spec = yaml.safe_load(api.requests.get("/schema.yml").content)
    validate(spec)
    return spec


def test_example_modules_expose_apps():
    for name in [
        "atelier",
        "fortunes",
        "helloworld",
        "lifespan",
        "marimo_mount",
        "rest_api",
        "sse_stream",
        "todo",
        "user",
        "websocket_chat",
    ]:
        module = importlib.import_module(f"examples.{name}")
        assert module.api is not None


def test_helloworld_example_preserves_cli_contract():
    assert helloworld.api.requests.get("/").text == "hello, world!"
    assert helloworld.api.requests.get("/hello").text == "hello, world!"
    assert helloworld.api.requests.get("/howdy").text == "howdy, world!"


def test_user_example_lifecycle_and_openapi():
    api = user.create_api(store=user.UserStore())
    spec = _openapi(api)

    assert spec["paths"]["/users"]["post"]["operationId"] == "create_user"

    response = api.requests.post(
        "/users",
        json={"username": "ada", "full_name": "Ada Lovelace"},
    )
    assert response.status_code == 201
    assert response.headers["location"] == "/users/1"
    assert response.json()["username"] == "ada"

    assert api.requests.get("/users/1").json()["full_name"] == "Ada Lovelace"
    missing = api.requests.get("/users/404")
    assert missing.status_code == 404
    assert missing.json()["user_id"] == 404


def test_rest_api_example_lifecycle_and_openapi():
    api = rest_api.create_api(store=rest_api.BookStore())
    spec = _openapi(api)

    assert spec["paths"]["/books"]["get"]["operationId"] == "list_books"

    response = api.requests.post(
        "/books",
        json={
            "title": "Kindred",
            "author": "Octavia E. Butler",
            "year": 1979,
        },
    )
    assert response.status_code == 201
    assert response.headers["location"] == "/books/1"

    assert api.requests.get("/books?author=butler").json()[0]["title"] == "Kindred"
    deleted = api.requests.delete("/books/1")
    assert deleted.status_code == 204
    assert deleted.headers["x-deleted-book"] == "1"
    assert deleted.content == b""


def test_lifespan_example_reports_ready_during_lifespan():
    api = lifespan.create_api()

    with TestClient(api) as client:
        assert client.get("/health").json() == {"ready": True}


def test_sse_stream_example_emits_events():
    api = sse_stream.create_api(event_count=2, delay=0)
    body = api.requests.get("/stream").text

    assert "event: tick" in body
    assert "id: 1" in body
    assert "data: Event #1" in body
    assert "id: 2" in body


def test_websocket_chat_example_echoes_to_room():
    api = websocket_chat.create_api()

    with TestClient(api).websocket_connect("/chat?name=Ada") as ws:
        assert ws.receive_text() == "Ada joined"
        ws.send_text("hello")
        assert ws.receive_text() == "Ada: hello"


def test_marimo_mount_example_serves_responder_endpoint():
    api = marimo_mount.create_api(mount_notebooks=False)

    assert api.requests.get("/hello").json() == {
        "message": "Hello from Responder!",
        "notebooks": "/notebooks/",
    }


def test_marimo_mount_example_has_clear_missing_dependency_response():
    if marimo_mount.marimo is not None:
        return

    api = marimo_mount.create_api()
    response = api.requests.get("/notebooks")

    assert response.status_code == 503
    assert response.json()["type"].endswith("/marimo-unavailable")
