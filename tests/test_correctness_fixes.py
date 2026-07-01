"""Regression tests for three correctness fixes:

- multipart form parsing no longer emits file parts / filenames as fields
- LoggingMiddleware tolerates a non-UTF-8 X-Request-ID
- descending sort keeps None values last (per the documented contract)
"""

import asyncio

from starlette.testclient import TestClient

import responder
from responder.ext.query import sort_items

# --------------------------------------------------------------------------
# Multipart form parsing (responder/formats.py)
# --------------------------------------------------------------------------


def _form_api():
    api = responder.API(allowed_hosts=[";"], session_https_only=False)

    @api.route("/form", methods=["POST"])
    async def form(req, resp):
        data = await req.media("form")
        resp.media = {key: data[key] for key in data}

    return api


def test_multipart_text_field_is_parsed():
    api = _form_api()
    r = TestClient(api, base_url="http://;").post("/form", data={"name": "widget"})
    assert r.json() == {"name": "widget"}


def test_multipart_excludes_files_and_does_not_leak_filenames():
    api = _form_api()
    # A UTF-8-decodable "file" plus a real field. The old parser harvested the
    # file's `filename` as a phantom field keyed by the filename.
    r = TestClient(api, base_url="http://;").post(
        "/form",
        data={"name": "widget"},
        files={"doc": ("notes.txt", b"hello world", "text/plain")},
    )
    body = r.json()
    assert body == {"name": "widget"}
    assert "notes.txt" not in body
    assert "doc" not in body


def test_multipart_multiple_fields_keep_their_names():
    api = _form_api()
    r = TestClient(api, base_url="http://;").post(
        "/form", data={"first": "a", "second": "b"}
    )
    assert r.json() == {"first": "a", "second": "b"}


# --------------------------------------------------------------------------
# Non-UTF-8 X-Request-ID (responder/ext/logging.py)
# --------------------------------------------------------------------------


def _drive_asgi(app, headers):
    """Call an ASGI app directly so we can send raw (non-UTF-8) header bytes
    that an HTTP client would refuse to encode."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("localhost", 80),
    }
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def test_non_utf8_request_id_does_not_crash():
    api = responder.API(allowed_hosts=["localhost"], enable_logging=True)

    @api.route("/")
    def index(req, resp):
        resp.text = "ok"

    # b"\xff\xfe" is valid latin-1 (what ASGI uses for headers) but invalid
    # UTF-8; the old UTF-8 decode raised in the outermost middleware and
    # dropped the request. A real HTTP client can't even send these bytes,
    # so drive the ASGI app directly.
    headers = [(b"host", b"localhost"), (b"x-request-id", b"\xff\xfe")]
    sent = _drive_asgi(api, headers)
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 200


# --------------------------------------------------------------------------
# Descending sort keeps None last (responder/ext/query.py)
# --------------------------------------------------------------------------


def test_sort_none_values_last_descending():
    rows = [{"v": 2}, {"v": None}, {"v": 3}, {"v": 1}]
    assert [x["v"] for x in sort_items(rows, "-v")] == [3, 2, 1, None]


def test_sort_none_values_last_ascending_still_holds():
    rows = [{"v": "b"}, {"v": None}, {"v": "a"}]
    assert [x["v"] for x in sort_items(rows, "v")] == ["a", "b", None]
