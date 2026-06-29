"""Tests for trailing-slash redirects, request size limits, auto-ETag,
after-response background tasks, and the Cache-Control helper."""

import threading
import time

import responder

# --- trailing-slash redirects ---


def test_trailing_slash_redirects_to_route(api):
    @api.route("/users")
    def users(req, resp):
        resp.media = ["alice"]

    r = api.requests.get("/users/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["Location"] == "/users"

    # And the client lands on the route when following.
    r = api.requests.get("/users/")
    assert r.json() == ["alice"]


def test_missing_slash_redirects_too(api):
    @api.route("/admin/")
    def admin(req, resp):
        resp.text = "admin"

    r = api.requests.get("/admin", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["Location"] == "/admin/"


def test_redirect_preserves_query_string(api):
    @api.route("/search")
    def search(req, resp):
        resp.media = {"q": req.params.get("q")}

    r = api.requests.get("/search/?q=hello", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["Location"] == "/search?q=hello"

    r = api.requests.get("/search/?q=hello")
    assert r.json() == {"q": "hello"}


def test_redirect_slashes_disabled():
    api = responder.API(redirect_slashes=False, allowed_hosts=[";"])

    @api.route("/users")
    def users(req, resp):
        resp.media = []

    assert api.requests.get("/users/", follow_redirects=False).status_code == 404


def test_no_redirect_for_truly_unknown_path(api):
    @api.route("/known")
    def known(req, resp):
        resp.text = "ok"

    assert api.requests.get("/unknown/", follow_redirects=False).status_code == 404


# --- request size limits ---


def test_max_request_size_rejects_large_bodies():
    api = responder.API(max_request_size=100, allowed_hosts=[";"])

    @api.route("/upload", methods=["POST"])
    async def upload(req, resp):
        body = await req.content
        resp.media = {"size": len(body)}

    r = api.requests.post("/upload", content=b"x" * 50)
    assert r.status_code == 200
    assert r.json() == {"size": 50}

    r = api.requests.post("/upload", content=b"x" * 200)
    assert r.status_code == 413


def test_max_request_size_enforced_on_stream():
    api = responder.API(max_request_size=100, allowed_hosts=[";"])

    @api.route("/upload", methods=["POST"])
    async def upload(req, resp):
        total = 0
        async for chunk in req.stream():
            total += len(chunk)
        resp.media = {"size": total}

    r = api.requests.post("/upload", content=b"x" * 500)
    assert r.status_code == 413


def test_max_request_size_413_negotiates_json():
    api = responder.API(max_request_size=10, allowed_hosts=[";"])

    @api.route("/upload", methods=["POST"])
    async def upload(req, resp):
        await req.content
        resp.text = "ok"

    r = api.requests.post(
        "/upload", content=b"x" * 100, headers={"Accept": "application/json"}
    )
    assert r.status_code == 413
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json() == {
        "type": "about:blank",
        "title": "Content Too Large",
        "status": 413,
        "detail": "Request body too large",
    }


def test_max_request_size_beats_body_model_validation():
    from pydantic import BaseModel

    class Item(BaseModel):
        name: str

    api = responder.API(max_request_size=10, allowed_hosts=[";"])

    @api.route("/items", methods=["POST"])
    async def create(req, resp, *, item: Item):
        resp.text = "ok"

    r = api.requests.post("/items", json={"name": "x" * 100})
    assert r.status_code == 413  # not a 422


def test_unlimited_by_default(api):
    @api.route("/upload", methods=["POST"])
    async def upload(req, resp):
        resp.media = {"size": len(await req.content)}

    r = api.requests.post("/upload", content=b"x" * 100_000)
    assert r.json() == {"size": 100_000}


# --- auto-ETag ---


def test_auto_etag_round_trip():
    api = responder.API(auto_etag=True, allowed_hosts=[";"])

    @api.route("/data")
    def data(req, resp):
        resp.media = {"stable": "content"}

    r = api.requests.get("/data")
    assert r.status_code == 200
    etag = r.headers["ETag"]
    assert etag

    r = api.requests.get("/data", headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert r.text == ""

    # Different content produces a different tag.
    @api.route("/other")
    def other(req, resp):
        resp.media = {"other": "content"}

    assert api.requests.get("/other").headers["ETag"] != etag


def test_auto_etag_skips_post():
    api = responder.API(auto_etag=True, allowed_hosts=[";"])

    @api.route("/submit", methods=["POST"])
    def submit(req, resp):
        resp.text = "created"

    r = api.requests.post("/submit")
    assert "ETag" not in r.headers


def test_explicit_etag_wins_over_auto():
    api = responder.API(auto_etag=True, allowed_hosts=[";"])

    @api.route("/doc")
    def doc(req, resp):
        resp.etag = "manual"
        resp.text = "body"

    assert api.requests.get("/doc").headers["ETag"] == '"manual"'


# --- after-response background tasks ---


def test_background_task_runs_after_response(api):
    ran = threading.Event()

    def task(value):
        ran.set()
        assert value == 42

    @api.route("/")
    def view(req, resp):
        resp.media = {"ok": True}
        resp.background(task, 42)

    r = api.requests.get("/")
    assert r.json() == {"ok": True}
    assert ran.wait(timeout=2)


def test_async_background_task(api):
    results = []

    async def task():
        results.append("done")

    @api.route("/")
    def view(req, resp):
        resp.text = "ok"
        resp.background(task)

    api.requests.get("/")
    deadline = time.time() + 2
    while not results and time.time() < deadline:
        time.sleep(0.01)
    assert results == ["done"]


def test_multiple_background_tasks_in_order(api):
    order = []

    @api.route("/")
    def view(req, resp):
        resp.text = "ok"
        resp.background(order.append, 1)
        resp.background(order.append, 2)

    api.requests.get("/")
    deadline = time.time() + 2
    while len(order) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert order == [1, 2]


# --- Cache-Control helper ---


def test_cache_control_directives(api):
    @api.route("/cached")
    def cached(req, resp):
        resp.cache_control(public=True, max_age=3600)
        resp.text = "ok"

    r = api.requests.get("/cached")
    assert r.headers["Cache-Control"] == "public, max-age=3600"


def test_cache_control_no_store(api):
    @api.route("/private")
    def private(req, resp):
        resp.cache_control(no_store=True, private=True)
        resp.text = "ok"

    r = api.requests.get("/private")
    assert r.headers["Cache-Control"] == "no-store, private"
