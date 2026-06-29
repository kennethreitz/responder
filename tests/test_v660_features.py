"""v7: route-local hooks, explicit dependencies, problem details, and ranges."""

import yaml
from pydantic import BaseModel
from starlette.testclient import TestClient

import responder
from responder import Depends, File, UploadFile
from responder.ext.auth import BearerAuth


def _api(**kwargs):
    return responder.API(
        title="T",
        version="1",
        openapi="3.0.2",
        secret_key="x" * 32,
        allowed_hosts=[";"],
        session_https_only=False,
        **kwargs,
    )


def _client(api):
    return TestClient(api, base_url="http://;", raise_server_exceptions=False)


def test_route_local_hooks_are_scoped_and_ordered():
    api = _api()
    seen = []

    def global_before(req, resp):
        seen.append("global-before")

    def local_before(req, resp):
        seen.append("local-before")
        req.state.local = True

    def local_after(req, resp):
        seen.append("local-after")
        resp.headers["X-Local"] = "1"

    def global_after(req, resp):
        seen.append("global-after")

    api.before_request(global_before)
    api.after_request(global_after)

    @api.get("/scoped", before=local_before, after=local_after)
    def scoped(req, resp):
        seen.append("handler")
        resp.media = {"local": req.state.local}

    @api.get("/plain")
    def plain(req, resp):
        seen.append("plain")
        resp.media = {}

    r = _client(api).get("/scoped")
    assert r.json() == {"local": True}
    assert r.headers["x-local"] == "1"
    assert seen == ["global-before", "local-before", "handler", "local-after", "global-after"]

    seen.clear()
    _client(api).get("/plain")
    assert seen == ["global-before", "plain", "global-after"]


def test_depends_injects_unregistered_provider_and_tears_down():
    api = _api()
    events = []

    def current_user(req):
        events.append("enter")
        yield req.headers["X-User"]
        events.append("exit")

    @api.get("/me")
    def me(req, resp, *, user=Depends(current_user)):
        resp.media = {"user": user}

    r = _client(api).get("/me", headers={"X-User": "kenneth"})
    assert r.json() == {"user": "kenneth"}
    assert events == ["enter", "exit"]


def test_route_auth_enforces_injects_and_documents_security():
    api = _api()
    auth = BearerAuth(tokens=["secret"])

    @api.get("/me", auth=auth)
    def me(req, resp, *, user):
        resp.media = {"user": user, "state_user": req.state.user}

    client = _client(api)
    assert client.get("/me").status_code == 401
    r = client.get("/me", headers={"Authorization": "Bearer secret"})
    assert r.json() == {"user": "secret", "state_user": "secret"}

    spec = yaml.safe_load(client.get("/schema.yml").content)
    assert spec["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert spec["paths"]["/me"]["get"]["security"] == [{"bearerAuth": []}]


def test_explicit_depends_param_wins_over_auth_injection():
    api = _api()
    events = []

    def auth(req):
        return "auth-user"

    def user_provider(req):
        events.append("dependency")
        return "depends-user"

    @api.get("/me", auth=auth)
    def me(req, resp, *, user=Depends(user_provider)):
        resp.media = {"param": user, "state": req.state.user}

    r = _client(api).get("/me")
    assert r.json() == {"param": "depends-user", "state": "auth-user"}
    assert events == ["dependency"]


def test_bound_method_depends_providers_do_not_share_cache():
    api = _api()

    class Provider:
        def __init__(self, value):
            self.value = value

        def provide(self):
            return self.value

    first = Provider("first")
    second = Provider("second")

    @api.get("/values")
    def values(req, resp, *, a=Depends(first.provide), b=Depends(second.provide)):
        resp.media = {"a": a, "b": b}

    assert _client(api).get("/values").json() == {"a": "first", "b": "second"}


def test_problem_details_for_framework_errors_by_default():
    api = _api()

    @api.get("/items/{id}")
    def item(req, resp, *, id: int):
        resp.media = {"id": id}

    client = _client(api)
    r = client.get("/missing")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["status"] == 404

    r = client.post("/items/1")
    assert r.status_code == 405
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["title"] == "Method Not Allowed"

    r = client.get("/items/nope")
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["title"] == "Validation Error"
    assert "errors" in r.json()


def test_problem_details_body_is_json_even_when_accept_prefers_yaml():
    class Out(BaseModel):
        id: int

    api = _api()

    @api.get("/items", response_model=Out)
    def items(req, resp):
        resp.media = {"id": "not-an-int"}

    r = _client(api).get("/items", headers={"Accept": "application/yaml"})
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.content.lstrip().startswith(b"{")
    assert r.json()["status"] == 500


def test_unhandled_500_uses_problem_details_by_default():
    api = _api()

    @api.get("/boom")
    def boom(req, resp):
        raise RuntimeError("boom")

    r = _client(api).get("/boom")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json() == {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
        "detail": "Internal Server Error",
    }


def test_after_hook_exception_uses_problem_details():
    api = _api()

    def broken_after(req, resp):
        raise RuntimeError("broken")

    @api.get("/items", after=broken_after)
    def items(req, resp):
        resp.media = {"ok": True}

    r = _client(api).get("/items")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["status"] == 500


def test_after_hook_failure_replaces_streamed_body():
    api = _api()

    def broken_after(req, resp):
        raise RuntimeError("broken")

    @api.get("/stream", after=broken_after)
    def stream(req, resp):
        @resp.stream
        async def body():
            yield b"original-stream-bytes"

    r = _client(api).get("/stream")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["status"] == 500
    assert b"original-stream-bytes" not in r.content


def test_after_hook_failure_replaces_deferred_file_body(tmp_path):
    fp = tmp_path / "data.txt"
    fp.write_text("original-file-bytes")

    api = _api()

    def broken_after(req, resp):
        raise RuntimeError("broken")

    @api.get("/file", after=broken_after)
    def file_(req, resp):
        resp.file(fp)

    r = _client(api).get("/file", headers={"Range": "bytes=0-4"})
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/problem+json")
    assert b"original-file-bytes" not in r.content
    assert "accept-ranges" not in r.headers
    assert "content-range" not in r.headers
    assert "etag" not in r.headers
    assert "last-modified" not in r.headers


def test_after_hook_failure_replaces_download_headers(tmp_path):
    fp = tmp_path / "data.txt"
    fp.write_text("original-file-bytes")

    api = _api()

    def broken_after(req, resp):
        raise RuntimeError("broken")

    @api.get("/download", after=broken_after)
    def download(req, resp):
        resp.download(fp)

    r = _client(api).get("/download")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/problem+json")
    assert "content-disposition" not in r.headers


def test_after_hook_failure_drops_background_tasks():
    api = _api()
    events = []

    def task():
        events.append("task")

    def broken_after(req, resp):
        raise RuntimeError("broken")

    @api.get("/items", after=broken_after)
    def items(req, resp):
        resp.media = {"ok": True}
        resp.background(task)

    r = _client(api).get("/items")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/problem+json")
    assert events == []


def test_after_hook_failure_drops_success_cookies():
    api = _api()

    def broken_after(req, resp):
        raise RuntimeError("broken")

    @api.get("/items", after=broken_after)
    def items(req, resp):
        resp.media = {"ok": True}
        resp.set_cookie("created", "yes")

    r = _client(api).get("/items")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/problem+json")
    assert "set-cookie" not in r.headers


def test_websocket_after_hook_failure_does_not_crash_task():
    api = _api()
    events = []

    def broken_after(ws):
        events.append("after")
        raise RuntimeError("broken")

    @api.route("/ws", websocket=True, after=broken_after)
    async def ws_endpoint(ws):
        events.append("handler")
        await ws.accept()
        await ws.send_text("ok")
        await ws.close()

    with _client(api).websocket_connect("ws://;/ws") as ws:
        assert ws.receive_text() == "ok"

    # The handler completed and the after-hook ran; its exception was swallowed
    # rather than escaping into the ASGI task.
    assert events == ["handler", "after"]


def test_problem_details_for_response_model_validation_failure_by_default():
    class Out(BaseModel):
        id: int

    api = _api()

    @api.get("/items", response_model=Out)
    def items(req, resp):
        resp.media = {"id": "not-an-int"}

    r = _client(api).get("/items")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Internal Server Error"
    assert body["status"] == 500
    assert body["detail"] == "Internal Server Error"
    assert len(body["errors"]) >= 1


def test_upload_file_save_helper(tmp_path):
    api = _api()
    target = tmp_path / "nested" / "saved.txt"

    @api.post("/upload")
    async def upload(req, resp, *, f: UploadFile = File(...)):
        saved = await f.save(target, create_parents=True)
        resp.media = {"path": saved.name, "content": target.read_text()}

    r = _client(api).post(
        "/upload", files={"f": ("input.txt", b"saved body", "text/plain")}
    )
    assert r.json() == {"path": "saved.txt", "content": "saved body"}


def test_multipart_byte_ranges_for_file_and_stream(tmp_path):
    fp = tmp_path / "data.txt"
    fp.write_text("0123456789")

    api = _api()

    @api.get("/file")
    def file_(req, resp):
        resp.file(fp)

    @api.get("/stream")
    def stream(req, resp):
        resp.stream_file(fp)

    for path in ("/file", "/stream"):
        r = _client(api).get(path, headers={"Range": "bytes=0-2,7-9"})
        assert r.status_code == 206
        assert r.headers["content-type"].startswith("multipart/byteranges; boundary=")
        assert "content-range" not in r.headers
        assert b"Content-Range: bytes 0-2/10" in r.content
        assert b"012" in r.content
        assert b"Content-Range: bytes 7-9/10" in r.content
        assert b"789" in r.content


def test_byte_ranges_are_coalesced_and_capped(tmp_path):
    fp = tmp_path / "data.txt"
    fp.write_text("0123456789")

    api = _api()

    @api.get("/file")
    def file_(req, resp):
        resp.file(fp)

    client = _client(api)
    r = client.get("/file", headers={"Range": "bytes=0-9,0-9"})
    assert r.status_code == 206
    assert r.headers["content-range"] == "bytes 0-9/10"
    assert r.content == b"0123456789"

    many = ",".join(["0-0"] * 64)
    r = client.get("/file", headers={"Range": f"bytes={many}"})
    assert r.status_code == 200
    assert r.content == b"0123456789"


def test_websocket_inline_depends_and_after_hook_run():
    api = _api()
    events = []

    def get_db(ws):
        events.append("dependency")
        return "db"

    def after(ws):
        events.append("after")

    @api.route("/ws", websocket=True, after=after)
    async def ws_endpoint(ws, *, db=Depends(get_db)):
        events.append("handler")
        await ws.accept()
        await ws.send_text(db)
        await ws.close()

    with _client(api).websocket_connect("ws://;/ws") as ws:
        assert ws.receive_text() == "db"

    assert events == ["dependency", "handler", "after"]
