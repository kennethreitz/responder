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
