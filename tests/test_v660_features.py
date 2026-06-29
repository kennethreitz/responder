"""v7: route-local hooks, explicit dependencies, problem details, and ranges."""

import yaml
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


def test_problem_details_for_framework_errors():
    api = _api(problem_details=True)

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
