"""Tests for HTTP semantics: 405/Allow, auto-OPTIONS, HEAD, SameSite cookies,
handler return values, and scoped route-group hooks."""

import asyncio

import responder


def _head_scope(path):
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "HEAD",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [(b"host", b";")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }


async def _collect_messages(app, scope):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    return messages


# --- 405 / Allow / OPTIONS / HEAD ---


def test_method_mismatch_returns_405_with_allow(api):
    @api.route("/items", methods=["GET"])
    def list_items(req, resp):
        resp.media = []

    @api.route("/items", methods=["POST"], check_existing=False)
    def create_item(req, resp):
        resp.status_code = 201

    r = api.requests.delete("/items")
    assert r.status_code == 405
    allow = r.headers["Allow"]
    assert "GET" in allow
    assert "POST" in allow
    assert "OPTIONS" in allow


def test_unknown_path_still_404(api):
    @api.route("/exists", methods=["GET"])
    def view(req, resp):
        resp.text = "ok"

    assert api.requests.get("/nowhere").status_code == 404


def test_unrestricted_route_never_405(api):
    @api.route("/anything")
    def view(req, resp):
        resp.text = "ok"

    assert api.requests.delete("/anything").status_code == 200


def test_automatic_options_response(api):
    @api.route("/items", methods=["GET", "POST"])
    def items(req, resp):
        resp.media = []

    r = api.requests.options("/items")
    assert r.status_code == 200
    assert "GET" in r.headers["Allow"]
    assert "POST" in r.headers["Allow"]


def test_head_supported_on_get_routes(api):
    @api.route("/page", methods=["GET"])
    def page(req, resp):
        resp.text = "content"

    r = api.requests.head("/page")
    assert r.status_code == 200

    # HEAD is advertised in Allow, and not available on non-GET routes.
    @api.route("/submit", methods=["POST"])
    def submit(req, resp):
        resp.text = "ok"

    assert api.requests.head("/submit").status_code == 405


def test_head_response_sends_headers_without_body():
    api = responder.API(
        allowed_hosts=[";"], session_https_only=False, gzip=False
    )

    @api.route("/page", methods=["GET"])
    def page(req, resp):
        resp.text = "content"

    messages = asyncio.run(_collect_messages(api, _head_scope("/page")))
    headers = dict(messages[0]["headers"])
    bodies = [
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    ]

    assert headers[b"content-length"] == b"7"
    assert bodies == [b""]


def test_head_stream_response_does_not_iterate_body():
    api = responder.API(
        allowed_hosts=[";"], session_https_only=False, gzip=False
    )
    events = []

    @api.route("/stream", methods=["GET"])
    def stream(req, resp):
        @resp.stream
        async def body():
            events.append("iterated")
            yield b"chunk"

    messages = asyncio.run(_collect_messages(api, _head_scope("/stream")))
    bodies = [
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    ]

    assert events == []
    assert bodies == [b""]


# --- SameSite cookies ---


def test_set_cookie_samesite_default(api):
    @api.route("/")
    def view(req, resp):
        resp.set_cookie("token", "abc")
        resp.text = "ok"

    r = api.requests.get("/")
    assert "samesite=lax" in r.headers["set-cookie"].lower()


def test_set_cookie_samesite_strict(api):
    @api.route("/")
    def view(req, resp):
        resp.set_cookie("token", "abc", samesite="strict")
        resp.text = "ok"

    r = api.requests.get("/")
    assert "samesite=strict" in r.headers["set-cookie"].lower()


def test_set_cookie_samesite_none_omits(api):
    @api.route("/")
    def view(req, resp):
        resp.set_cookie("token", "abc", samesite=None)
        resp.text = "ok"

    r = api.requests.get("/")
    assert "samesite" not in r.headers["set-cookie"].lower()


# --- handler return values ---


def test_return_dict_sets_media(api):
    @api.route("/")
    def view(req, resp):
        return {"hello": "world"}

    r = api.requests.get("/")
    assert r.json() == {"hello": "world"}


def test_return_list_sets_media(api):
    @api.route("/")
    async def view(req, resp):
        return [1, 2, 3]

    assert api.requests.get("/").json() == [1, 2, 3]


def test_return_str_sets_text(api):
    @api.route("/")
    def view(req, resp):
        return "plain text"

    r = api.requests.get("/")
    assert r.text == "plain text"
    assert "text/plain" in r.headers["content-type"]


def test_return_bytes_sets_content(api):
    @api.route("/")
    def view(req, resp):
        return b"raw bytes"

    assert api.requests.get("/").content == b"raw bytes"


def test_return_none_keeps_resp_mutation(api):
    @api.route("/")
    def view(req, resp):
        resp.media = {"set": "directly"}

    assert api.requests.get("/").json() == {"set": "directly"}


def test_return_value_in_class_based_view(api):
    @api.route("/resource")
    class Resource:
        def on_get(self, req, resp):
            return {"method": "get"}

    assert api.requests.get("/resource").json() == {"method": "get"}


# --- scoped route group hooks ---


def test_group_before_request_scoped_to_prefix(api):
    v1 = api.group("/v1")

    @v1.before_request()
    def require_key(req, resp):
        if "X-Api-Key" not in req.headers:
            resp.status_code = 401
            resp.media = {"error": "missing key"}

    @v1.route("/data")
    def v1_data(req, resp):
        resp.media = {"v": 1}

    @api.route("/public")
    def public(req, resp):
        resp.media = {"open": True}

    # The hook guards the group...
    assert api.requests.get("/v1/data").status_code == 401
    assert (
        api.requests.get("/v1/data", headers={"X-Api-Key": "k"}).status_code == 200
    )
    # ...but not routes outside it.
    assert api.requests.get("/public").status_code == 200


def test_group_before_request_async_and_prefix_boundary(api):
    v1 = api.group("/v1")
    calls = []

    @v1.before_request()
    async def observe(req, resp):
        calls.append(req.url.path)

    @v1.route("/a")
    def a(req, resp):
        resp.text = "a"

    # "/v1x" must not be treated as part of the "/v1" group.
    @api.route("/v1x")
    def v1x(req, resp):
        resp.text = "x"

    api.requests.get("/v1/a")
    api.requests.get("/v1x")
    assert calls == ["/v1/a"]


# --- validated request body injection ---


def test_validated_body_model_injected(api):
    from pydantic import BaseModel

    class Item(BaseModel):
        name: str
        price: float

    @api.route("/items", methods=["POST"])
    async def create(req, resp, *, item: Item):
        resp.media = {"name": item.name, "price": item.price}

    r = api.requests.post("/items", json={"name": "tea", "price": 4.5})
    assert r.status_code == 200
    assert r.json() == {"name": "tea", "price": 4.5}

    r = api.requests.post("/items", json={"name": "tea"})
    assert r.status_code == 422
