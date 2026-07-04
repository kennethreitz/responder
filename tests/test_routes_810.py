"""Tests for the 8.1 routing additions: Pydantic body-model injection for
class-based views, Query/Header/Cookie markers on WebSocket handlers,
Pydantic form-model binding via ``Form(...)``, and automatic OPTIONS
responses for class-based views."""

import pytest
from pydantic import BaseModel, ConfigDict, Field
from starlette.testclient import TestClient as StarletteTestClient
from starlette.websockets import WebSocketDisconnect

from responder import Cookie, Depends, Form, Header, Query, UploadFile


class Item(BaseModel):
    name: str
    price: float


# --- Class-based view body-model injection ---


def test_cbv_body_model_injection_on_post(api, session, url):
    @api.route("/items")
    class ItemResource:
        def on_post(self, req, resp, *, item: Item):
            resp.media = {"created": item.model_dump()}

    r = session.post(url("/items"), json={"name": "wrench", "price": 9.5})
    assert r.status_code == 200
    assert r.json() == {"created": {"name": "wrench", "price": 9.5}}


def test_cbv_body_model_injection_on_put_and_patch(api, session, url):
    @api.route("/items/{item_id}")
    class ItemResource:
        def on_put(self, req, resp, *, item_id, item: Item):
            resp.media = {"id": item_id, "put": item.name}

        def on_patch(self, req, resp, *, item_id, item: Item):
            resp.media = {"id": item_id, "patch": item.name}

    r = session.put(url("/items/7"), json={"name": "bolt", "price": 1.0})
    assert r.status_code == 200
    assert r.json() == {"id": "7", "put": "bolt"}

    r = session.patch(url("/items/7"), json={"name": "nut", "price": 0.5})
    assert r.status_code == 200
    assert r.json() == {"id": "7", "patch": "nut"}


def test_cbv_body_model_invalid_body_returns_422(api, session, url):
    @api.route("/items")
    class ItemResource:
        def on_post(self, req, resp, *, item: Item):
            resp.media = item.model_dump()

    r = session.post(url("/items"), json={"name": "wrench"})  # price missing
    assert r.status_code == 422
    assert any("price" in str(err.get("loc", "")) for err in r.json()["errors"])


def test_cbv_body_model_non_object_body_returns_422(api, session, url):
    @api.route("/items")
    class ItemResource:
        def on_post(self, req, resp, *, item: Item):
            resp.media = item.model_dump()

    r = session.post(url("/items"), json=[1, 2, 3])
    assert r.status_code == 422


def test_cbv_shared_model_parsed_once_across_views(api, session, url):
    """on_request and on_post declaring the same model share one instance."""
    seen = {}

    @api.route("/items")
    class ItemResource:
        def on_request(self, req, resp, *, item: Item):
            seen["on_request"] = id(item)

        def on_post(self, req, resp, *, item: Item):
            seen["on_post"] = id(item)
            resp.media = item.model_dump()

    r = session.post(url("/items"), json={"name": "x", "price": 2.0})
    assert r.status_code == 200
    assert seen["on_request"] == seen["on_post"]


def test_cbv_body_model_excludes_path_and_dependency_names(api, session, url):
    api.add_dependency("dep_item", lambda: "from-dep")

    @api.route("/things/{thing}")
    class ThingResource:
        def on_post(self, req, resp, *, thing, dep_item, item: Item):
            resp.media = {
                "thing": thing,
                "dep": dep_item,
                "item": item.model_dump(),
            }

    r = session.post(url("/things/abc"), json={"name": "n", "price": 3.0})
    assert r.status_code == 200
    body = r.json()
    assert body["thing"] == "abc"
    assert body["dep"] == "from-dep"
    assert body["item"] == {"name": "n", "price": 3.0}


def test_function_view_body_injection_unchanged(api, session, url):
    @api.route("/fn-items", methods=["POST"])
    async def create(req, resp, *, item: Item):
        resp.media = item.model_dump()

    r = session.post(url("/fn-items"), json={"name": "y", "price": 4.5})
    assert r.status_code == 200
    assert r.json() == {"name": "y", "price": 4.5}

    r = session.post(url("/fn-items"), json={"name": "y"})
    assert r.status_code == 422


# --- Markers on class-based view methods (regression coverage) ---


def test_cbv_query_marker(api, session, url):
    @api.route("/search")
    class SearchResource:
        def on_get(self, req, resp, *, q: str = Query(...), limit: int = Query(10)):
            resp.media = {"q": q, "limit": limit}

    r = session.get(url("/search?q=hello&limit=3"))
    assert r.status_code == 200
    assert r.json() == {"q": "hello", "limit": 3}

    r = session.get(url("/search"))
    assert r.status_code == 422


def test_cbv_header_and_cookie_markers(api, session, url):
    @api.route("/who")
    class WhoResource:
        def on_get(
            self,
            req,
            resp,
            *,
            user_agent: str = Header("unknown"),
            session_id: str = Cookie("anon"),
        ):
            resp.media = {"ua": user_agent, "sid": session_id}

    session.cookies.set("session_id", "s-1")
    r = session.get(url("/who"), headers={"User-Agent": "test-agent"})
    assert r.status_code == 200
    assert r.json() == {"ua": "test-agent", "sid": "s-1"}


# --- Class-based view automatic OPTIONS ---


def test_cbv_auto_options_returns_200_with_allow(api, session, url):
    @api.route("/resource")
    class Resource:
        def on_get(self, req, resp):
            resp.text = "get"

        def on_post(self, req, resp):
            resp.text = "post"

    r = session.options(url("/resource"))
    assert r.status_code == 200
    assert r.headers["Allow"] == "GET, HEAD, OPTIONS, POST"
    assert r.content == b""


def test_cbv_auto_options_with_path_params(api, session, url):
    @api.route("/items/{item_id}")
    class Resource:
        def on_get(self, req, resp, *, item_id):
            resp.text = item_id

    r = session.options(url("/items/42"))
    assert r.status_code == 200
    assert r.headers["Allow"] == "GET, HEAD, OPTIONS"


def test_cbv_explicit_on_options_wins(api, session, url):
    @api.route("/resource")
    class Resource:
        def on_get(self, req, resp):
            resp.text = "get"

        def on_options(self, req, resp):
            resp.status_code = 200
            resp.headers["Allow"] = "GET, OPTIONS"
            resp.media = {"custom": True}

    r = session.options(url("/resource"))
    assert r.status_code == 200
    assert r.json() == {"custom": True}
    assert r.headers["Allow"] == "GET, OPTIONS"


def test_cbv_on_request_still_handles_options(api, session, url):
    """A catch-all on_request keeps receiving OPTIONS (no auto short-circuit)."""

    @api.route("/resource")
    class Resource:
        def on_request(self, req, resp):
            resp.media = {"method": req.method}

    r = session.options(url("/resource"))
    assert r.status_code == 200
    assert r.json()["method"].upper() == "OPTIONS"


def test_cbv_405_allow_header_includes_options(api, session, url):
    @api.route("/resource")
    class Resource:
        def on_get(self, req, resp):
            resp.text = "get"

    r = session.post(url("/resource"))
    assert r.status_code == 405
    assert r.headers["Allow"] == "GET, HEAD, OPTIONS"


# --- Query()/Header()/Cookie() markers on WebSocket handlers ---


def test_ws_query_marker(api):
    @api.route("/ws", websocket=True)
    async def handler(ws, *, token: str = Query(...)):
        await ws.accept()
        await ws.send_text(token)
        await ws.close()

    client = StarletteTestClient(api)
    with client.websocket_connect("ws://;/ws?token=abc123") as ws:
        assert ws.receive_text() == "abc123"


def test_ws_query_marker_coerces_type(api):
    @api.route("/ws", websocket=True)
    async def handler(ws, *, room: int = Query(...)):
        await ws.accept()
        await ws.send_json({"room": room, "type": type(room).__name__})
        await ws.close()

    client = StarletteTestClient(api)
    with client.websocket_connect("ws://;/ws?room=42") as ws:
        assert ws.receive_json() == {"room": 42, "type": "int"}


def test_ws_query_marker_default_applies(api):
    @api.route("/ws", websocket=True)
    async def handler(ws, *, room: str = Query("lobby")):
        await ws.accept()
        await ws.send_text(room)
        await ws.close()

    client = StarletteTestClient(api)
    with client.websocket_connect("ws://;/ws") as ws:
        assert ws.receive_text() == "lobby"


def test_ws_missing_required_query_marker_closes_1008(api):
    @api.route("/ws", websocket=True)
    async def handler(ws, *, token: str = Query(...)):
        await ws.accept()  # pragma: no cover - never reached

    client = StarletteTestClient(api)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("ws://;/ws"):
            pass
    assert excinfo.value.code == 1008


def test_ws_invalid_query_marker_closes_1008(api):
    @api.route("/ws", websocket=True)
    async def handler(ws, *, room: int = Query(...)):
        await ws.accept()  # pragma: no cover - never reached

    client = StarletteTestClient(api)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("ws://;/ws?room=nope"):
            pass
    assert excinfo.value.code == 1008


def test_ws_header_and_cookie_markers(api):
    @api.route("/ws", websocket=True)
    async def handler(
        ws,
        *,
        x_token: str = Header(...),
        session_id: str = Cookie("anon"),
    ):
        await ws.accept()
        await ws.send_json({"token": x_token, "sid": session_id})
        await ws.close()

    client = StarletteTestClient(api)
    with client.websocket_connect(
        "ws://;/ws",
        headers={"X-Token": "secret", "Cookie": "session_id=s-9"},
    ) as ws:
        assert ws.receive_json() == {"token": "secret", "sid": "s-9"}


def test_ws_markers_compose_with_path_params_and_depends(api):
    def greeting():
        return "hi"

    @api.route("/ws/{room}", websocket=True)
    async def handler(ws, *, room, token: str = Query(...), word=Depends(greeting)):
        await ws.accept()
        await ws.send_json({"room": room, "token": token, "word": word})
        await ws.close()

    client = StarletteTestClient(api)
    with client.websocket_connect("ws://;/ws/lobby?token=t") as ws:
        assert ws.receive_json() == {"room": "lobby", "token": "t", "word": "hi"}


# --- Pydantic form-model binding ---


class ProfileForm(BaseModel):
    name: str
    age: int = 0


def test_form_model_urlencoded(api, session, url):
    @api.route("/profiles", methods=["POST"])
    async def create(req, resp, *, profile: ProfileForm = Form(...)):
        resp.media = profile.model_dump()

    r = session.post(url("/profiles"), data={"name": "kenneth", "age": "37"})
    assert r.status_code == 200
    assert r.json() == {"name": "kenneth", "age": 37}


def test_form_model_defaults_apply(api, session, url):
    @api.route("/profiles", methods=["POST"])
    async def create(req, resp, *, profile: ProfileForm = Form(...)):
        resp.media = profile.model_dump()

    r = session.post(url("/profiles"), data={"name": "kenneth"})
    assert r.status_code == 200
    assert r.json() == {"name": "kenneth", "age": 0}


def test_form_model_missing_required_field_422(api, session, url):
    @api.route("/profiles", methods=["POST"])
    async def create(req, resp, *, profile: ProfileForm = Form(...)):
        resp.media = profile.model_dump()  # pragma: no cover - never reached

    r = session.post(url("/profiles"), data={"age": "3"})
    assert r.status_code == 422
    errors = r.json()["errors"]
    assert any(err["loc"] == ["form", "name"] for err in errors)


def test_form_model_coercion_failure_422(api, session, url):
    @api.route("/profiles", methods=["POST"])
    async def create(req, resp, *, profile: ProfileForm = Form(...)):
        resp.media = profile.model_dump()  # pragma: no cover - never reached

    r = session.post(url("/profiles"), data={"name": "k", "age": "not-a-number"})
    assert r.status_code == 422
    errors = r.json()["errors"]
    assert any(err["loc"] == ["form", "age"] for err in errors)


def test_form_model_optional_with_default(api, session, url):
    @api.route("/profiles", methods=["POST"])
    async def create(req, resp, *, profile: ProfileForm = Form(None)):
        resp.media = {"got": profile.model_dump() if profile else None}

    # An empty form falls back to the marker default.
    r = session.post(
        url("/profiles"),
        data=b"",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 200
    assert r.json() == {"got": None}

    r = session.post(url("/profiles"), data={"name": "k"})
    assert r.status_code == 200
    assert r.json() == {"got": {"name": "k", "age": 0}}


def test_form_model_list_field_collects_repeats(api, session, url):
    class TagsForm(BaseModel):
        title: str
        tags: list[str] = []

    @api.route("/posts", methods=["POST"])
    async def create(req, resp, *, post: TagsForm = Form(...)):
        resp.media = post.model_dump()

    r = session.post(url("/posts"), data={"title": "t", "tags": ["a", "b"]})
    assert r.status_code == 200
    assert r.json() == {"title": "t", "tags": ["a", "b"]}


def test_form_model_field_alias(api, session, url):
    class AliasedForm(BaseModel):
        full_name: str = Field(alias="fullName")

    @api.route("/aliased", methods=["POST"])
    async def create(req, resp, *, data: AliasedForm = Form(...)):
        resp.media = {"name": data.full_name}

    r = session.post(url("/aliased"), data={"fullName": "Kenneth Reitz"})
    assert r.status_code == 200
    assert r.json() == {"name": "Kenneth Reitz"}


def test_form_model_multipart_with_upload_file(api, session, url):
    class AvatarForm(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        name: str
        avatar: UploadFile

    @api.route("/avatars", methods=["POST"])
    async def create(req, resp, *, form: AvatarForm = Form(...)):
        content = await form.avatar.read()
        resp.media = {
            "name": form.name,
            "filename": form.avatar.filename,
            "bytes": len(content),
        }

    r = session.post(
        url("/avatars"),
        data={"name": "k"},
        files={"avatar": ("pic.png", b"\x89PNG fake", "image/png")},
    )
    assert r.status_code == 200
    assert r.json() == {"name": "k", "filename": "pic.png", "bytes": 9}


def test_form_model_on_cbv_method(api, session, url):
    @api.route("/cbv-profiles")
    class ProfileResource:
        def on_post(self, req, resp, *, profile: ProfileForm = Form(...)):
            resp.media = profile.model_dump()

    r = session.post(url("/cbv-profiles"), data={"name": "cbv", "age": "1"})
    assert r.status_code == 200
    assert r.json() == {"name": "cbv", "age": 1}


def test_scalar_form_markers_still_work(api, session, url):
    @api.route("/upload", methods=["POST"])
    async def upload(req, resp, *, title: str = Form(...), tags: list[str] = Form([])):
        resp.media = {"title": title, "tags": tags}

    r = session.post(url("/upload"), data={"title": "t", "tags": ["x"]})
    assert r.status_code == 200
    assert r.json() == {"title": "t", "tags": ["x"]}


def test_form_model_not_treated_as_json_body(api, session, url):
    """A Form()-marked model param must not also trigger JSON body injection."""

    @api.route("/mixed", methods=["POST"])
    async def create(req, resp, *, profile: ProfileForm = Form(...)):
        resp.media = profile.model_dump()

    # Sent as a form, not JSON — the marker pipeline handles it.
    r = session.post(url("/mixed"), data={"name": "form-wins", "age": "2"})
    assert r.status_code == 200
    assert r.json() == {"name": "form-wins", "age": 2}
