"""Regression and feature tests for the v4.1 framework-wide improvements.

Organized by the batch that introduced each behavior.
"""

import pytest

import responder


@pytest.fixture
def make_api():
    """Build an API bound to the test host, with overridable kwargs."""

    def _make(**kwargs):
        kwargs.setdefault("allowed_hosts", [";"])
        return responder.API(**kwargs)

    return _make


# ---------------------------------------------------------------------------
# Batch 1 — correctness & resource-safety
# ---------------------------------------------------------------------------


def test_mount_prefix_does_not_capture_sibling_path(make_api):
    """`/subscribe` must not be mis-routed into an app mounted at `/sub`."""
    api = make_api()

    seen = {}

    async def subapp(scope, receive, send):
        seen["path"] = scope["path"]
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [[b"content-type", b"text/plain"]],
            }
        )
        await send({"type": "http.response.body", "body": b"sub"})

    api.mount("/sub", subapp)

    # Sibling path that merely shares the prefix string: must NOT hit the mount.
    r = api.requests.get("/subscribe")
    assert "path" not in seen
    assert r.status_code == 404

    # Real sub-paths keep their leading slash.
    seen.clear()
    api.requests.get("/sub/inner")
    assert seen["path"] == "/inner"

    seen.clear()
    api.requests.get("/sub")
    assert seen["path"] == "/"


def test_more_specific_mount_wins(make_api):
    """Longer (more specific) mount prefixes resolve before shorter ones."""
    api = make_api()
    hits = []

    def make_app(name):
        async def app(scope, receive, send):
            hits.append((name, scope["path"]))
            await send(
                {"type": "http.response.start", "status": 200, "headers": []}
            )
            await send({"type": "http.response.body", "body": name.encode()})

        return app

    api.mount("/api", make_app("api"))
    api.mount("/api/v2", make_app("v2"))

    api.requests.get("/api/v2/users")
    assert hits == [("v2", "/users")]


def test_dependency_teardown_isolation(make_api):
    """One failing teardown must not strand the others."""
    api = make_api()
    closed = []

    @api.dependency()
    def first():
        yield "a"
        closed.append("first")

    @api.dependency()
    def second():
        yield "b"
        raise RuntimeError("teardown boom")

    @api.route("/")
    def view(req, resp, *, first, second):
        resp.text = first + second

    r = api.requests.get("/")
    assert r.status_code == 200
    assert r.text == "ab"
    # `second` (resolved last) tears down first and raises; `first` must
    # still get its teardown despite that.
    assert closed == ["first"]


def test_max_request_size_rejects_oversized_body(make_api):
    api = make_api(max_request_size=16)

    @api.route("/", methods=["POST"])
    async def view(req, resp):
        resp.text = await req.text

    big = api.requests.post("/", content=b"x" * 100)
    assert big.status_code == 413

    ok = api.requests.post("/", content=b"small")
    assert ok.status_code == 200
    assert ok.text == "small"


def test_empty_body_reads_cleanly(make_api):
    api = make_api()

    @api.route("/", methods=["POST"])
    async def view(req, resp):
        body = await req.content
        resp.media = {"len": len(body)}

    r = api.requests.post("/", content=b"")
    assert r.json() == {"len": 0}


def test_resp_file_serves_and_supports_ranges(make_api, tmp_path):
    api = make_api()
    f = tmp_path / "data.txt"
    f.write_text("hello file")

    @api.route("/f")
    def view(req, resp):
        resp.file(str(f))

    r = api.requests.get("/f")
    assert r.status_code == 200
    assert r.text == "hello file"

    part = api.requests.get("/f", headers={"Range": "bytes=0-4"})
    assert part.status_code == 206
    assert part.text == "hello"
    assert part.headers["Content-Range"] == "bytes 0-4/10"


def test_background_call_awaits_and_returns(make_api):
    api = make_api()
    ran = []

    @api.route("/")
    async def view(req, resp):
        async def work():
            ran.append(True)
            return 42

        out = await api.background(work)
        resp.media = {"out": out}

    r = api.requests.get("/")
    assert r.json() == {"out": 42}
    assert ran == [True]


def test_server_session_resists_fixation(make_api):
    from responder.ext.sessions import MemorySessionBackend

    api = make_api(session_backend=MemorySessionBackend())

    @api.route("/login", methods=["POST"])
    async def login(req, resp):
        req.session["user"] = "kenneth"
        resp.text = "ok"

    client = api.requests
    client.cookies.set("responder_session", "attacker-planted-id")
    r = client.post("/login")
    set_cookie = r.headers.get("set-cookie", "")
    # The server must mint a fresh id, never adopt the attacker's planted one.
    assert "attacker-planted-id" not in set_cookie
    assert "responder_session=" in set_cookie


# ---------------------------------------------------------------------------
# Batch 2 — security footguns (additive)
# ---------------------------------------------------------------------------


def test_default_secret_key_warns(make_api, caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="responder"):
        make_api()
    assert any("default secret key" in r.message for r in caplog.records)


def test_custom_secret_key_does_not_warn(make_api, caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="responder"):
        make_api(secret_key="a-real-private-secret")
    assert not any("default secret key" in r.message for r in caplog.records)


def test_session_cookie_name_is_configurable(make_api):
    api = make_api(session_cookie="myapp_sess", secret_key="x")

    @api.route("/", methods=["POST"])
    async def view(req, resp):
        req.session["k"] = "v"
        resp.text = "ok"

    r = api.requests.post("/")
    assert "myapp_sess=" in r.headers.get("set-cookie", "")


def test_regenerate_session_rotates_id(make_api):
    import re

    from responder.ext.sessions import MemorySessionBackend, regenerate_session

    backend = MemorySessionBackend()
    api = make_api(session_backend=backend)

    @api.route("/set", methods=["POST"])
    async def setup(req, resp):
        req.session["user"] = "kenneth"
        resp.text = "ok"

    @api.route("/login", methods=["POST"])
    async def login(req, resp):
        req.session["user"] = "kenneth"
        regenerate_session(req)
        resp.text = "ok"

    client = api.requests
    r1 = client.post("/set")
    id1 = re.search(r"responder_session=([^;]+)", r1.headers["set-cookie"]).group(1)

    r2 = client.post("/login")
    id2 = re.search(r"responder_session=([^;]+)", r2.headers["set-cookie"]).group(1)

    assert id2 != id1
    assert backend.get(id1) is None  # old record discarded
    assert backend.get(id2) == {"user": "kenneth"}


def _graphql_api(make_api, **gql):
    graphene = pytest.importorskip("graphene")

    class Query(graphene.ObjectType):
        hello = graphene.String()

        def resolve_hello(self, info):
            return "hi"

    api = make_api()
    api.graphql("/graphql", schema=graphene.Schema(query=Query), **gql)
    return api


def test_graphql_introspection_can_be_disabled(make_api):
    api = _graphql_api(make_api, introspection=False)
    r = api.requests.post(
        "/graphql", json={"query": "{ __schema { types { name } } }"}
    )
    assert r.status_code == 400
    assert "introspection" in str(r.json()).lower()


def test_graphql_introspection_on_by_default(make_api):
    api = _graphql_api(make_api)
    r = api.requests.post(
        "/graphql", json={"query": "{ __schema { queryType { name } } }"}
    )
    assert r.status_code == 200
    assert "data" in r.json()


def test_graphql_max_depth_rejects_deep_queries(make_api):
    graphene = pytest.importorskip("graphene")

    class Inner(graphene.ObjectType):
        value = graphene.String()

    class Outer(graphene.ObjectType):
        inner = graphene.Field(Inner)

    class Query(graphene.ObjectType):
        outer = graphene.Field(Outer)

    api = make_api()
    api.graphql("/g", schema=graphene.Schema(query=Query), max_depth=2)
    r = api.requests.post("/g", json={"query": "{ outer { inner { value } } }"})
    assert r.status_code == 400
    assert "depth" in str(r.json()).lower()


def test_graphiql_can_be_disabled(make_api):
    api = _graphql_api(make_api, graphiql=False)
    r = api.requests.get("/graphql", headers={"Accept": "text/html"})
    assert "graphiql" not in r.text.lower()

    api2 = _graphql_api(make_api, graphiql=True)
    r2 = api2.requests.get("/graphql", headers={"Accept": "text/html"})
    assert "graphiql" in r2.text.lower()


def test_resp_file_root_blocks_traversal(make_api, tmp_path):
    (tmp_path / "safe.txt").write_text("safe")
    (tmp_path.parent / "secret.txt").write_text("secret")
    api = make_api()

    @api.route("/f")
    def view(req, resp):
        resp.file(req.params.get("name"), root=str(tmp_path))

    ok = api.requests.get("/f", params={"name": "safe.txt"})
    assert ok.status_code == 200
    assert ok.text == "safe"

    bad = api.requests.get("/f", params={"name": "../secret.txt"})
    assert bad.status_code == 404


def test_redirect_blocks_external_when_disallowed(make_api):
    api = make_api()

    @api.route("/ext")
    def ext(req, resp):
        resp.redirect("https://evil.example.com/phish", allow_external=False)

    @api.route("/proto")
    def proto(req, resp):
        resp.redirect("//evil.example.com", allow_external=False)

    @api.route("/safe")
    def safe(req, resp):
        resp.redirect("/dashboard", allow_external=False)

    assert api.requests.get("/ext", follow_redirects=False).status_code == 400
    assert api.requests.get("/proto", follow_redirects=False).status_code == 400

    r = api.requests.get("/safe", follow_redirects=False)
    assert r.status_code in (301, 302, 307)
    assert r.headers["location"] == "/dashboard"


# ---------------------------------------------------------------------------
# Batch 3 — familiar-framework ergonomics
# ---------------------------------------------------------------------------


def test_malformed_json_returns_400(make_api):
    api = make_api()

    @api.route("/", methods=["POST"])
    async def view(req, resp):
        resp.media = await req.media()

    r = api.requests.post(
        "/", content=b"{bad json", headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 400


def test_malformed_yaml_returns_400(make_api):
    api = make_api()

    @api.route("/", methods=["POST"])
    async def view(req, resp):
        resp.media = await req.media("yaml")

    r = api.requests.post(
        "/", content=b"key: : : bad\n  - x", headers={"Content-Type": "application/x-yaml"}
    )
    assert r.status_code == 400


def test_tuple_return_sets_status(make_api):
    api = make_api()

    @api.route("/", methods=["POST"])
    def view(req, resp):
        return {"created": True}, 201

    r = api.requests.post("/")
    assert r.status_code == 201
    assert r.json() == {"created": True}


def test_tuple_return_with_headers(make_api):
    api = make_api()

    @api.route("/")
    def view(req, resp):
        return {"ok": True}, 200, {"X-Custom": "yes"}

    r = api.requests.get("/")
    assert r.json() == {"ok": True}
    assert r.headers["X-Custom"] == "yes"


def test_abort_helper(make_api):
    api = make_api()

    @api.route("/admin")
    def admin(req, resp):
        responder.abort(403, detail="nope")

    r = api.requests.get("/admin", headers={"Accept": "application/json"})
    assert r.status_code == 403
    assert r.json() == {"error": "nope"}


def test_before_request_bare_decorator(make_api):
    api = make_api()
    calls = []

    @api.before_request
    def hook(req, resp):
        calls.append("before")

    @api.route("/")
    def view(req, resp):
        resp.text = "ok"

    api.requests.get("/")
    assert calls == ["before"]


def test_after_request_bare_decorator(make_api):
    api = make_api()

    @api.after_request
    def hook(req, resp):
        resp.headers["X-After"] = "1"

    @api.route("/")
    def view(req, resp):
        resp.text = "ok"

    r = api.requests.get("/")
    assert r.headers["X-After"] == "1"


def test_resp_ok_does_not_raise_before_status(make_api):
    api = make_api()

    @api.route("/")
    def view(req, resp):
        resp.media = {"ok_before": resp.ok}

    assert api.requests.get("/").json() == {"ok_before": True}


def test_auto_escape_flag_is_honored(make_api, tmp_path):
    tdir = tmp_path / "templates"
    tdir.mkdir()
    (tdir / "t.html").write_text("{{ v }}")

    off = make_api(templates_dir=str(tdir), auto_escape=False)

    @off.route("/")
    def view_off(req, resp):
        resp.text = off.template("t.html", v="<b>hi</b>")

    assert off.requests.get("/").text == "<b>hi</b>"

    on = make_api(templates_dir=str(tdir))  # default True

    @on.route("/")
    def view_on(req, resp):
        resp.text = on.template("t.html", v="<b>hi</b>")

    assert "&lt;b&gt;" in on.requests.get("/").text


def test_media_sync_in_sync_handler(make_api):
    api = make_api()

    @api.route("/", methods=["POST"])
    def view(req, resp):
        resp.media = {"got": req.media_sync()}

    r = api.requests.post("/", json={"x": 1})
    assert r.json() == {"got": {"x": 1}}


def test_text_sync_in_sync_handler(make_api):
    api = make_api()

    @api.route("/", methods=["POST"])
    def view(req, resp):
        resp.text = req.text_sync

    r = api.requests.post("/", content=b"hello")
    assert r.text == "hello"


# ---------------------------------------------------------------------------
# Batch 4 — first-class Pydantic models
# ---------------------------------------------------------------------------


def test_resp_media_accepts_pydantic_model(make_api):
    from pydantic import BaseModel

    class Item(BaseModel):
        id: int
        name: str

    api = make_api()

    @api.route("/")
    def view(req, resp):
        resp.media = Item(id=1, name="x")

    assert api.requests.get("/").json() == {"id": 1, "name": "x"}


def test_return_pydantic_model(make_api):
    from pydantic import BaseModel

    class Item(BaseModel):
        id: int

    api = make_api()

    @api.route("/")
    def view(req, resp):
        return Item(id=7)

    assert api.requests.get("/").json() == {"id": 7}


def test_native_types_serialize(make_api):
    from datetime import datetime
    from decimal import Decimal
    from uuid import UUID

    api = make_api()
    uid = UUID("12345678-1234-5678-1234-567812345678")

    @api.route("/")
    def view(req, resp):
        resp.media = {
            "when": datetime(2026, 1, 2, 3, 4, 5),
            "id": uid,
            "price": Decimal("9.99"),
            "tags": {"a"},
        }

    data = api.requests.get("/").json()
    assert data["when"] == "2026-01-02T03:04:05"
    assert data["id"] == str(uid)
    assert data["price"] == 9.99
    assert data["tags"] == ["a"]


def test_dataclass_serializes(make_api):
    import dataclasses

    @dataclasses.dataclass
    class Point:
        x: int
        y: int

    api = make_api()

    @api.route("/")
    def view(req, resp):
        return Point(1, 2)

    assert api.requests.get("/").json() == {"x": 1, "y": 2}


def test_native_types_serialize_to_yaml(make_api):
    from datetime import datetime

    api = make_api()

    @api.route("/")
    def view(req, resp):
        resp.media = {"when": datetime(2026, 1, 2)}

    r = api.requests.get("/", headers={"Accept": "application/x-yaml"})
    assert "2026-01-02" in r.text


def test_response_model_strips_and_coerces(make_api):
    from pydantic import BaseModel

    class Out(BaseModel):
        id: int
        name: str

    api = make_api()

    @api.route("/", response_model=Out)
    def view(req, resp):
        resp.media = {"id": "5", "name": "x", "secret": "leak"}

    data = api.requests.get("/").json()
    assert data == {"id": 5, "name": "x"}  # coerced + extra field stripped


def test_response_model_fails_closed_in_prod(make_api):
    from pydantic import BaseModel

    class Out(BaseModel):
        id: int

    api = make_api(debug=False)

    @api.route("/", response_model=Out)
    def view(req, resp):
        resp.media = {"id": "not-an-int", "secret": "leak"}

    r = api.requests.get("/")
    assert r.status_code == 500
    assert "leak" not in r.text  # never emit the unvalidated payload


def test_response_model_raises_in_debug(make_api):
    from pydantic import BaseModel

    class Out(BaseModel):
        id: int

    api = make_api(debug=True)

    @api.route("/", response_model=Out)
    def view(req, resp):
        resp.media = {"id": "not-an-int"}

    with pytest.raises(Exception):  # noqa: B017 - validation surfaces in debug
        api.requests.get("/")


def test_response_model_validates_list(make_api):
    from pydantic import BaseModel

    class Out(BaseModel):
        id: int

    api = make_api()

    @api.route("/", response_model=Out)
    def view(req, resp):
        resp.media = [{"id": "1", "x": "extra"}, {"id": 2}]

    assert api.requests.get("/").json() == [{"id": 1}, {"id": 2}]


def test_pluggable_json_encoder(make_api):
    import json

    calls = []

    def enc(media):
        calls.append(media)
        return json.dumps({"wrapped": media})

    api = make_api(json_dumps=enc)

    @api.route("/")
    def view(req, resp):
        resp.media = {"x": 1}

    r = api.requests.get("/")
    assert r.json() == {"wrapped": {"x": 1}}
    assert calls == [{"x": 1}]
