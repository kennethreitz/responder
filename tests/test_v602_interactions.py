"""v6.0.2: regression tests for the cross-feature interaction bugs."""

import yaml
from pydantic import BaseModel
from starlette.testclient import TestClient

import responder
from responder import File, UploadFile


def _api(**kwargs):
    return responder.API(
        allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False, **kwargs
    )


def _client(api):
    return TestClient(api, base_url="http://;")


def _file_app(tmp_path, **kwargs):
    fp = tmp_path / "data.txt"
    fp.write_text("hello world")
    api = _api(**kwargs)

    @api.route("/f")
    def f(req, resp):
        resp.file(str(fp))

    return _client(api)


# --- #1 If-Modified-Since naive timezone must not 500 -----------------------


def test_if_modified_since_naive_tz_does_not_crash(tmp_path):
    client = _file_app(tmp_path)
    r = client.get("/f", headers={"If-Modified-Since": "Wed, 15 Nov 2023 22:13:20 -0000"})
    assert r.status_code in (200, 304)  # not a 500


# --- #2 multipart parse then re-read the body (no "Stream consumed") --------


def test_media_files_then_media_form():
    api = _api()

    @api.route("/u", methods=["POST"])
    async def u(req, resp):
        files = await req.media("files")
        form = await req.media("form")
        resp.media = {"files": list(files), "name": form.get("name")}

    r = _client(api).post(
        "/u", files={"doc": ("x.txt", b"hi", "text/plain")}, data={"name": "alice"}
    )
    assert r.json() == {"files": ["doc"], "name": "alice"}


def test_file_marker_then_req_content():
    api = _api()

    @api.route("/m", methods=["POST"])
    async def m(req, resp, *, f: UploadFile = File(...)):
        body = await req.content  # must not raise "Stream consumed"
        resp.media = {"name": f.filename, "body_len": len(body)}

    r = _client(api).post("/m", files={"f": ("x.txt", b"hello", "text/plain")})
    assert r.json()["name"] == "x.txt"
    assert r.json()["body_len"] > 0


# --- #3 max_request_size enforced on multipart ------------------------------


def test_max_request_size_enforced_for_multipart():
    api = _api(max_request_size=50)

    @api.route("/u", methods=["POST"])
    async def u(req, resp, *, f: UploadFile = File(...)):
        resp.media = {"ok": True}

    r = _client(api).post("/u", files={"f": ("x", b"A" * 500, "text/plain")})
    assert r.status_code == 413


# --- #4 recursive model produces a real OpenAPI component -------------------


def test_recursive_model_openapi_component():
    class Node(BaseModel):
        value: int
        children: list["Node"] = []

    api = _api(title="T", version="1", openapi="3.0.2")

    @api.route("/tree", methods=["POST"], request_model=Node)
    async def tree(req, resp):
        resp.media = {}

    spec = yaml.safe_load(_client(api).get("/schema.yml").content)
    node = spec["components"]["schemas"]["Node"]
    assert "properties" in node and "value" in node["properties"]
    assert "$ref" not in node  # not a bare self-ref wrapper


# --- #5 APIKeyAuth(query) works on WebSocket routes -------------------------


def test_api_key_query_on_websocket():
    from responder.ext.auth import APIKeyAuth

    api = _api()
    auth = APIKeyAuth(keys=["k"], name="api_key", location="query")
    api.add_dependency("key", auth)

    @api.route("/ws", websocket=True)
    async def ws(ws, *, key):
        await ws.accept()
        await ws.send_text(key)
        await ws.close()

    with TestClient(api).websocket_connect("ws://;/ws?api_key=k") as conn:
        assert conn.receive_text() == "k"


# --- #6 callable-instance generator dependency runs teardown ----------------


def test_callable_instance_generator_dependency_teardown():
    cleaned = []

    class Resource:
        def __call__(self):
            yield "resource"
            cleaned.append(True)

    api = _api()
    api.add_dependency("res", Resource())

    @api.route("/r")
    def r(req, resp, *, res):
        resp.media = {"res": res}

    assert _client(api).get("/r").json() == {"res": "resource"}
    assert cleaned == [True]  # teardown ran


# --- #7 override an app-dep that another app-dep depends on ------------------


def test_override_reaches_into_app_scope_graph():
    api = _api()

    @api.dependency(scope="app")
    def config():
        return {"env": "prod"}

    @api.dependency(scope="app")
    def db(config):
        return f"db-{config['env']}"

    @api.route("/d")
    def d(req, resp, *, db):
        resp.media = {"db": db}

    client = _client(api)
    assert client.get("/d").json() == {"db": "db-prod"}  # warm the app cache
    with api.dependency_overrides(config={"env": "test"}):
        assert client.get("/d").json() == {"db": "db-test"}
    assert client.get("/d").json() == {"db": "db-prod"}  # restored


# --- #9 304 carries the negotiated Vary ------------------------------------


def test_not_modified_includes_vary():
    api = _api(auto_etag=True)  # auto_vary defaults on in 6.0

    @api.route("/j")
    def j(req, resp):
        resp.media = {"ok": True}

    client = _client(api)
    etag = client.get("/j").headers["etag"]
    r = client.get("/j", headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert r.headers.get("vary") == "Accept"


# --- #10 Range + matching validator returns 304, not 206 --------------------


def test_conditional_wins_over_range(tmp_path):
    client = _file_app(tmp_path)
    etag = client.get("/f").headers["etag"]
    r = client.get("/f", headers={"If-None-Match": etag, "Range": "bytes=0-2"})
    assert r.status_code == 304


# --- #11 nested overrides restore to the enclosing value --------------------


def test_nested_overrides_restore_per_key():
    api = _api()

    @api.dependency()
    def db():
        return "real"

    @api.route("/db")
    def view(req, resp, *, db):
        resp.media = {"db": db}

    client = _client(api)
    with api.dependency_overrides(db="outer"):
        with api.dependency_overrides(db="inner"):
            assert client.get("/db").json() == {"db": "inner"}
        assert client.get("/db").json() == {"db": "outer"}  # restored to outer
    assert client.get("/db").json() == {"db": "real"}  # fully restored
