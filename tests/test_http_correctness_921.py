"""Correctness fixes for redirects, jailed files, ranges, and Accept q-values."""

import pytest


def test_redirect_blocks_control_character_open_redirects(api):
    # Header serialization drops NUL/CR/LF, and browsers also drop tabs and
    # treat backslash as slash. The external check has to see that URL.
    payloads = [
        "/\x00/evil.example",
        "/\x00\\evil.example",
        "\\\x00/evil.example",
        "/\t/evil.example",
        "/\r/evil.example",
        "/\n\\evil.example",
    ]

    for index, payload in enumerate(payloads):

        @api.route(f"/r{index}")
        def view(req, resp, *, _payload=payload):
            resp.redirect(_payload, allow_external=False)

    for index in range(len(payloads)):
        response = api.requests.get(f"/r{index}", follow_redirects=False)
        assert response.status_code == 400
        assert response.headers.get("location") is None


def test_redirect_emits_normalized_location(api):
    @api.route("/safe")
    def safe(req, resp):
        resp.redirect("/dash\x00board", allow_external=False)

    @api.route("/ext")
    def ext(req, resp):
        # External redirects stay allowed, but the header is the URL the
        # client will follow — not the pre-scrub string.
        resp.redirect("/\x00/evil.example")

    safe_response = api.requests.get("/safe", follow_redirects=False)
    assert safe_response.status_code == 307
    assert safe_response.headers["location"] == "/dashboard"
    assert "\x00" not in safe_response.text

    external = api.requests.get("/ext", follow_redirects=False)
    assert external.status_code == 307
    assert external.headers["location"] == "//evil.example"


def test_rooted_file_rejects_unsafe_and_missing_paths(api, tmp_path):
    (tmp_path / "safe.txt").write_text("safe")
    (tmp_path / "subdir").mkdir()
    loop = tmp_path / "loop"
    loop.symlink_to("loop")

    @api.route("/file")
    def file_view(req, resp):
        resp.file(req.headers["x-name"], root=str(tmp_path))

    @api.route("/stream")
    def stream_view(req, resp):
        resp.stream_file(req.headers["x-name"], root=str(tmp_path))

    @api.route("/download")
    def download_view(req, resp):
        resp.download(req.headers["x-name"], root=str(tmp_path))

    for path in ("/file", "/stream", "/download"):
        ok = api.requests.get(path, headers={"x-name": "safe.txt"})
        assert ok.status_code == 200
        assert ok.content == b"safe"

        for name in ("nope.txt", "foo\x00bar", "subdir", "loop", "../secret.txt"):
            blocked = api.requests.get(path, headers={"x-name": name})
            assert blocked.status_code == 404, (path, name, blocked.status_code)


def test_unrooted_missing_file_still_raises(api, tmp_path):
    missing = tmp_path / "missing.bin"

    @api.route("/missing")
    def missing_view(req, resp):
        resp.file(missing)

    with pytest.raises(FileNotFoundError):
        api.requests.get("/missing")


def test_range_unit_is_case_insensitive_and_spaced(api, tmp_path):
    media = tmp_path / "media.bin"
    media.write_bytes(bytes(range(32)))

    @api.route("/media")
    def view(req, resp):
        resp.file(media)

    ranged = api.requests.get("/media", headers={"Range": "Bytes = 0-3"})
    assert ranged.status_code == 206
    assert ranged.headers["content-range"] == "bytes 0-3/32"
    assert ranged.content == bytes(range(4))

    untouched = api.requests.get("/media", headers={"Range": "items=0-3"})
    assert untouched.status_code == 200
    assert len(untouched.content) == 32


def test_empty_file_byte_range_is_416(api, tmp_path):
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")

    @api.route("/empty")
    def view(req, resp):
        resp.file(empty)

    full = api.requests.get("/empty")
    assert full.status_code == 200
    assert full.content == b""

    for header in ("bytes=0-0", "bytes=-1", "Bytes=0-"):
        response = api.requests.get("/empty", headers={"Range": header})
        assert response.status_code == 416, header
        assert response.headers["content-range"] == "bytes */0"
        assert response.content == b""

    # A malformed range is ignored, same as on a non-empty file.
    malformed = api.requests.get("/empty", headers={"Range": "bytes=abc"})
    assert malformed.status_code == 200
    assert malformed.content == b""


def test_out_of_range_accept_q_is_ignored(api):
    seen = {}

    @api.route("/")
    def view(req, resp):
        seen["json"] = req._accept_quality("application/json")
        seen["yaml"] = req._accept_quality("application/yaml")
        resp.media = {"ok": True}

    ranked = api.requests.get(
        "/",
        headers={"Accept": "application/yaml;q=2, application/json;q=1"},
    )
    assert seen == {"json": 1.0, "yaml": 1.0}
    assert ranked.headers["content-type"].startswith("application/json")
    assert ranked.json() == {"ok": True}

    non_finite = api.requests.get(
        "/", headers={"Accept": "application/json;q=nan, */*;q=0.2"}
    )
    # The invalid q is dropped, so the explicit JSON range stays at q=1
    # and still beats the wildcard.
    assert seen["json"] == 1.0
    assert non_finite.headers["content-type"].startswith("application/json")

    excluded = api.requests.get(
        "/", headers={"Accept": "application/json;q=0, application/yaml;q=0.8"}
    )
    assert seen["json"] == 0.0
    assert seen["yaml"] == 0.8
    assert excluded.headers["content-type"].startswith("application/yaml")
