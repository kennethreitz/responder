import importlib
import json
import py_compile

import yaml
from openapi_spec_validator import validate
from starlette.testclient import TestClient

from examples import (
    helloworld,
    lifespan,
    marimo_mount,
    rest_api,
    shortlinks,
    sse_stream,
    tarot,
    user,
    webhooks,
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
        "shortlinks",
        "sse_stream",
        "tarot",
        "todo",
        "user",
        "webhooks",
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

    root = api.requests.get("/", follow_redirects=False)
    assert root.status_code == 301
    assert root.headers["location"] == "/notebooks/"
    assert api.requests.get("/hello").json() == {
        "message": "Hello from Responder!",
        "notebooks": "/notebooks/",
    }


def test_marimo_mount_preserves_notebook_asset_paths():
    class FakeServer:
        def __init__(self):
            self.apps = []

        def with_app(self, *, path, root):
            self.apps.append((path, root))
            return self

        def build(self):
            async def app(scope, receive, send):
                body = json.dumps(
                    {
                        "path": scope["path"],
                        "root_path": scope.get("root_path", ""),
                    }
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [[b"content-type", b"application/json"]],
                    }
                )
                await send({"type": "http.response.body", "body": body})

            return app

    class FakeMarimo:
        def __init__(self):
            self.server = FakeServer()

        def create_asgi_app(self):
            return self.server

    fake = FakeMarimo()
    api = marimo_mount.create_api(marimo_module=fake)

    assert fake.server.apps == [
        ("/notebooks", str(marimo_mount.NOTEBOOK_PATH))
    ]
    root = api.requests.get("/", follow_redirects=False)
    assert root.status_code == 301
    assert root.headers["location"] == "/notebooks/"
    assert api.requests.get("/hello").json()["message"] == "Hello from Responder!"
    assert api.requests.get("/notebooks/").json() == {
        "path": "/notebooks/",
        "root_path": "",
    }
    assert api.requests.get("/notebooks/assets/index.css").json() == {
        "path": "/notebooks/assets/index.css",
        "root_path": "",
    }


def test_marimo_mount_example_notebook_exists_and_compiles():
    assert marimo_mount.NOTEBOOK_PATH.exists()
    py_compile.compile(str(marimo_mount.NOTEBOOK_PATH), doraise=True)


def test_marimo_mount_example_has_clear_missing_dependency_response():
    if marimo_mount.marimo is not None:
        return

    api = marimo_mount.create_api()
    response = api.requests.get("/notebooks")

    assert response.status_code == 503
    assert response.json()["type"].endswith("/marimo-unavailable")


def test_tarot_example_lists_cards_and_deals_seeded_spread():
    api = tarot.create_api(deck=tarot.TarotDeck())
    spec = _openapi(api)

    assert spec["paths"]["/deal"]["post"]["operationId"] == "deal_tarot_cards"

    cards = api.requests.get("/cards").json()
    assert len(cards) == 78
    assert cards[0]["slug"] == "the-fool"

    major = api.requests.get("/cards?arcana=major").json()
    cups = api.requests.get("/cards?suit=cups").json()
    assert len(major) == 22
    assert len(cups) == 14

    fool = api.requests.get("/cards/the-fool").json()
    assert fool["name"] == "The Fool"
    assert fool["number"] == 0

    deal = api.requests.post(
        "/deal",
        json={
            "spread": "past-present-future",
            "seed": "responder",
            "allow_reversed": False,
        },
    )
    assert deal.status_code == 200
    body = deal.json()
    assert body["count"] == 3
    assert [card["position"] for card in body["cards"]] == [
        "Past",
        "Present",
        "Future",
    ]
    assert {card["orientation"] for card in body["cards"]} == {"upright"}


def test_shortlinks_example_redirects_and_tracks_clicks():
    api = shortlinks.create_api(store=shortlinks.LinkStore())
    spec = _openapi(api)

    assert spec["paths"]["/links"]["post"]["operationId"] == "create_short_link"

    created = api.requests.post(
        "/links",
        json={
            "code": "Docs",
            "title": "Responder docs",
            "destination": "https://responder.kennethreitz.org",
        },
    )
    assert created.status_code == 201
    assert created.headers["location"] == "/links/docs"
    assert created.json()["code"] == "docs"

    duplicate = api.requests.post(
        "/links",
        json={
            "code": "docs",
            "destination": "https://example.com",
        },
    )
    assert duplicate.status_code == 409

    redirected = api.requests.get("/r/docs", follow_redirects=False)
    assert redirected.status_code == 302
    assert redirected.headers["location"] == "https://responder.kennethreitz.org"

    details = api.requests.get("/links/docs").json()
    assert details["clicks"] == 1
    assert details["last_clicked_at"] is not None


def test_webhooks_example_verifies_signature_before_accepting_event():
    secret = "unit-test-secret"
    api = webhooks.create_api(store=webhooks.WebhookStore(), secret=secret)
    spec = _openapi(api)

    assert (
        spec["paths"]["/webhooks/events"]["post"]["operationId"]
        == "receive_webhook_event"
    )

    body = b'{"type":"invoice.paid","data":{"invoice":"in_123"}}'
    bad = api.requests.post(
        "/webhooks/events",
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": "sha256=bad"},
    )
    assert bad.status_code == 401
    assert api.requests.get("/events").json() == []

    signature = webhooks.sign_payload(body, secret)
    accepted = api.requests.post(
        "/webhooks/events",
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": signature},
    )
    assert accepted.status_code == 202
    payload = accepted.json()
    assert payload["accepted"] is True
    assert payload["event"]["id"] == 1
    assert payload["event"]["type"] == "invoice.paid"

    events = api.requests.get("/events").json()
    assert len(events) == 1
