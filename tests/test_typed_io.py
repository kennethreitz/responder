"""v5: type-hint-driven handler I/O (Query/Header/Cookie/Path + return model)."""

import pytest

import responder
from responder import Cookie, Header, Path, Query


@pytest.fixture
def make_api():
    def _make(**kwargs):
        kwargs.setdefault("allowed_hosts", [";"])
        kwargs.setdefault("session_https_only", False)
        return responder.API(**kwargs)

    return _make


def test_query_marker_validates_and_coerces(make_api):
    api = make_api()

    @api.route("/search")
    def search(req, resp, *, q: str = Query(...), limit: int = Query(10)):
        resp.media = {"q": q, "limit": limit, "limit_type": type(limit).__name__}

    r = api.requests.get("/search?q=hello&limit=5")
    assert r.json() == {"q": "hello", "limit": 5, "limit_type": "int"}


def test_query_marker_uses_default(make_api):
    api = make_api()

    @api.route("/search")
    def search(req, resp, *, limit: int = Query(10)):
        resp.media = {"limit": limit}

    assert api.requests.get("/search").json() == {"limit": 10}


def test_required_query_marker_missing_returns_422(make_api):
    api = make_api()

    @api.route("/search")
    def search(req, resp, *, q: str = Query(...)):
        resp.text = q

    r = api.requests.get("/search")
    assert r.status_code == 422
    assert "errors" in r.json()


def test_query_marker_invalid_type_returns_422(make_api):
    api = make_api()

    @api.route("/search")
    def search(req, resp, *, limit: int = Query(...)):
        resp.media = {"limit": limit}

    r = api.requests.get("/search?limit=notanumber")
    assert r.status_code == 422


def test_query_marker_sequence(make_api):
    api = make_api()

    @api.route("/items")
    def items(req, resp, *, ids: list[int] = Query(...)):
        resp.media = {"ids": ids}

    r = api.requests.get("/items?ids=1&ids=2&ids=3")
    assert r.json() == {"ids": [1, 2, 3]}


def test_header_marker(make_api):
    api = make_api()

    @api.route("/")
    def view(req, resp, *, user_agent: str = Header("none")):
        resp.media = {"ua": user_agent}

    r = api.requests.get("/", headers={"User-Agent": "responder-test"})
    assert r.json() == {"ua": "responder-test"}


def test_cookie_marker(make_api):
    api = make_api()

    @api.route("/")
    def view(req, resp, *, theme: str = Cookie("light")):
        resp.media = {"theme": theme}

    client = api.requests
    client.cookies.set("theme", "dark")
    assert client.get("/").json() == {"theme": "dark"}


def test_path_marker_and_query_together(make_api):
    api = make_api()

    @api.route("/users/{user_id}/posts")
    def posts(req, resp, *, user_id: int = Path(...), page: int = Query(1)):
        resp.media = {"user_id": user_id, "page": page}

    r = api.requests.get("/users/7/posts?page=2")
    assert r.json() == {"user_id": 7, "page": 2}


def test_return_annotation_is_response_model(make_api):
    from pydantic import BaseModel

    class ItemOut(BaseModel):
        id: int
        name: str

    api = make_api()

    @api.route("/item")
    def item(req, resp) -> ItemOut:
        # extra field is stripped, id coerced, per the declared model
        resp.media = {"id": "1", "name": "widget", "secret": "x"}

    assert api.requests.get("/item").json() == {"id": 1, "name": "widget"}


def test_markers_coexist_with_body_injection(make_api):
    from pydantic import BaseModel

    class Payload(BaseModel):
        value: int

    api = make_api()

    @api.route("/submit", methods=["POST"])
    async def submit(req, resp, *, payload: Payload, trace: str = Header("none")):
        resp.media = {"value": payload.value, "trace": trace}

    r = api.requests.post(
        "/submit", json={"value": 42}, headers={"trace": "abc"}
    )
    assert r.json() == {"value": 42, "trace": "abc"}
