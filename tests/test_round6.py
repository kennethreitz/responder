"""Tests for HTTP range requests, downloads, request timeouts, and
route-resolution caching."""

import time

import pytest

import responder

# --- range requests ---


@pytest.fixture
def media_file(tmp_path):
    f = tmp_path / "media.bin"
    f.write_bytes(bytes(range(256)) * 4)  # 1024 bytes
    return f


def test_file_serves_full_with_accept_ranges(api, media_file):
    @api.route("/media")
    def media(req, resp):
        resp.file(media_file)

    r = api.requests.get("/media")
    assert r.status_code == 200
    assert r.headers["Accept-Ranges"] == "bytes"
    assert len(r.content) == 1024


def test_file_range_request(api, media_file):
    @api.route("/media")
    def media(req, resp):
        resp.file(media_file)

    r = api.requests.get("/media", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206
    assert r.headers["Content-Range"] == "bytes 0-9/1024"
    assert r.content == bytes(range(10))


def test_file_suffix_and_open_ranges(api, media_file):
    @api.route("/media")
    def media(req, resp):
        resp.file(media_file)

    # Last 4 bytes.
    r = api.requests.get("/media", headers={"Range": "bytes=-4"})
    assert r.status_code == 206
    assert r.headers["Content-Range"] == "bytes 1020-1023/1024"
    assert r.content == bytes([252, 253, 254, 255])

    # From offset to end.
    r = api.requests.get("/media", headers={"Range": "bytes=1020-"})
    assert r.status_code == 206
    assert r.content == bytes([252, 253, 254, 255])


def test_file_unsatisfiable_range_416(api, media_file):
    @api.route("/media")
    def media(req, resp):
        resp.file(media_file)

    r = api.requests.get("/media", headers={"Range": "bytes=5000-"})
    assert r.status_code == 416
    assert r.headers["Content-Range"] == "bytes */1024"


def test_file_malformed_range_serves_full(api, media_file):
    @api.route("/media")
    def media(req, resp):
        resp.file(media_file)

    r = api.requests.get("/media", headers={"Range": "bytes=abc-def"})
    assert r.status_code == 200
    assert len(r.content) == 1024


def test_stream_file_range_request(api, media_file):
    @api.route("/media")
    def media(req, resp):
        resp.stream_file(media_file, chunk_size=7)

    r = api.requests.get("/media", headers={"Range": "bytes=100-149"})
    assert r.status_code == 206
    assert r.headers["Content-Range"] == "bytes 100-149/1024"
    assert r.content == (bytes(range(256)) * 4)[100:150]


def test_stream_file_full_still_works(api, media_file):
    @api.route("/media")
    def media(req, resp):
        resp.stream_file(media_file)

    r = api.requests.get("/media")
    assert r.status_code == 200
    assert len(r.content) == 1024


def test_range_ignored_for_post(api, media_file):
    @api.route("/media", methods=["POST"])
    def media(req, resp):
        resp.file(media_file)

    r = api.requests.post("/media", headers={"Range": "bytes=0-9"})
    assert r.status_code == 200
    assert len(r.content) == 1024


# --- downloads ---


def test_download_sets_content_disposition(api, tmp_path):
    f = tmp_path / "report.csv"
    f.write_text("a,b\n1,2\n")

    @api.route("/export")
    def export(req, resp):
        resp.download(f)

    r = api.requests.get("/export")
    assert r.headers["Content-Disposition"] == 'attachment; filename="report.csv"'
    assert r.text == "a,b\n1,2\n"


def test_download_custom_and_unicode_filename(api, tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"x")

    @api.route("/a")
    def a(req, resp):
        resp.download(f, filename="résumé.pdf")

    r = api.requests.get("/a")
    assert r.headers["Content-Disposition"] == (
        "attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf"
    )


def test_download_is_resumable(api, tmp_path):
    f = tmp_path / "big.bin"
    f.write_bytes(b"0123456789")

    @api.route("/dl")
    def dl(req, resp):
        resp.download(f)

    r = api.requests.get("/dl", headers={"Range": "bytes=5-"})
    assert r.status_code == 206
    assert r.content == b"56789"


# --- request timeouts ---


def test_request_timeout_returns_504():
    api = responder.API(request_timeout=0.1, allowed_hosts=[";"])

    @api.route("/slow")
    async def slow(req, resp):
        import asyncio

        await asyncio.sleep(2)
        resp.text = "too late"

    start = time.time()
    r = api.requests.get("/slow")
    assert time.time() - start < 1.5
    assert r.status_code == 504
    assert "timed out" in r.text


def test_request_timeout_json_negotiation():
    api = responder.API(request_timeout=0.1, allowed_hosts=[";"])

    @api.route("/slow")
    async def slow(req, resp):
        import asyncio

        await asyncio.sleep(2)

    r = api.requests.get("/slow", headers={"Accept": "application/json"})
    assert r.status_code == 504
    assert r.json() == {"error": "Request timed out"}


def test_fast_handlers_unaffected_by_timeout():
    api = responder.API(request_timeout=5, allowed_hosts=[";"])

    @api.route("/fast")
    def fast(req, resp):
        resp.text = "ok"

    assert api.requests.get("/fast").text == "ok"


def test_timeout_runs_dependency_teardown():
    api = responder.API(request_timeout=0.1, allowed_hosts=[";"])
    events = []

    @api.dependency()
    def res():
        events.append("open")
        yield "r"
        events.append("close")

    @api.route("/slow")
    async def slow(req, resp, *, res):
        import asyncio

        await asyncio.sleep(2)

    r = api.requests.get("/slow")
    assert r.status_code == 504
    assert events == ["open", "close"]


# --- route resolution caching ---


def test_cached_resolution_returns_fresh_path_params(api):
    @api.route("/items/{id:int}")
    def item(req, resp, *, id):
        resp.media = {"id": id}

    # Same concrete path twice — second hit comes from the cache.
    assert api.requests.get("/items/7").json() == {"id": 7}
    assert api.requests.get("/items/7").json() == {"id": 7}
    # A different path still resolves correctly.
    assert api.requests.get("/items/9").json() == {"id": 9}


def test_route_cache_invalidated_on_new_route(api):
    @api.route("/a")
    def a(req, resp):
        resp.text = "a"

    assert api.requests.get("/a").text == "a"
    assert api.requests.get("/b").status_code == 404

    @api.route("/b")
    def b(req, resp):
        resp.text = "b"

    assert api.requests.get("/b").text == "b"


def test_route_cache_distinguishes_methods(api):
    @api.route("/r", methods=["GET"])
    def read(req, resp):
        resp.text = "read"

    @api.route("/r", methods=["POST"], check_existing=False)
    def write(req, resp):
        resp.text = "write"

    assert api.requests.get("/r").text == "read"
    assert api.requests.post("/r").text == "write"
    assert api.requests.get("/r").text == "read"
