"""v5.4: stat-based ETag + Last-Modified for served files."""

from starlette.testclient import TestClient

import responder


def _app(tmp_path):
    fp = tmp_path / "data.txt"
    fp.write_text("hello world, this is a file")

    api = responder.API(allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False)

    @api.route("/f")
    def f(req, resp):
        resp.file(str(fp))

    @api.route("/s")
    def s(req, resp):
        resp.stream_file(str(fp))

    @api.route("/d")
    def d(req, resp):
        resp.download(str(fp))

    @api.route("/noc")
    def noc(req, resp):
        resp.file(str(fp), conditional=False)

    return TestClient(api, base_url="http://;")


def test_file_sends_etag_and_last_modified(tmp_path):
    r = _app(tmp_path).get("/f")
    assert r.status_code == 200
    assert r.headers["etag"].startswith('W/"')
    assert "last-modified" in r.headers


def test_if_none_match_returns_304(tmp_path):
    client = _app(tmp_path)
    etag = client.get("/f").headers["etag"]
    r = client.get("/f", headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""


def test_if_modified_since_returns_304(tmp_path):
    client = _app(tmp_path)
    last_modified = client.get("/f").headers["last-modified"]
    r = client.get("/f", headers={"If-Modified-Since": last_modified})
    assert r.status_code == 304


def test_stream_and_download_are_conditional(tmp_path):
    client = _app(tmp_path)
    for path in ("/s", "/d"):
        etag = client.get(path).headers["etag"]
        assert client.get(path, headers={"If-None-Match": etag}).status_code == 304


def test_range_still_served(tmp_path):
    r = _app(tmp_path).get("/f", headers={"Range": "bytes=0-4"})
    assert r.status_code == 206
    assert r.content == b"hello"


def test_if_range_last_modified_serves_partial(tmp_path):
    client = _app(tmp_path)
    last_modified = client.get("/f").headers["last-modified"]
    r = client.get("/f", headers={"Range": "bytes=0-4", "If-Range": last_modified})
    assert r.status_code == 206
    assert r.content == b"hello"


def test_if_range_stale_last_modified_falls_back_to_full(tmp_path):
    r = _app(tmp_path).get(
        "/f",
        headers={
            "Range": "bytes=0-4",
            "If-Range": "Thu, 01 Jan 1970 00:00:00 GMT",
        },
    )
    assert r.status_code == 200
    assert r.content == b"hello world, this is a file"


def test_if_range_strong_etag_is_honored(tmp_path):
    fp = tmp_path / "data.txt"
    fp.write_text("hello world, this is a file")

    api = responder.API(allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False)

    @api.route("/f")
    def f(req, resp):
        resp.etag = '"v1"'
        resp.file(str(fp))

    client = TestClient(api, base_url="http://;")
    r = client.get("/f", headers={"Range": "bytes=0-4", "If-Range": '"v1"'})
    assert r.status_code == 206
    assert r.content == b"hello"


def test_if_range_applies_to_downloads(tmp_path):
    client = _app(tmp_path)
    last_modified = client.get("/d").headers["last-modified"]
    r = client.get("/d", headers={"Range": "bytes=0-4", "If-Range": last_modified})
    assert r.status_code == 206
    assert r.content == b"hello"


def test_conditional_opt_out(tmp_path):
    r = _app(tmp_path).get("/noc")
    assert "etag" not in r.headers
    assert r.content == b"hello world, this is a file"
