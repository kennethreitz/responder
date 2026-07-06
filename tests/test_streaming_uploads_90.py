"""v9.0: streaming multipart uploads and bounded request bodies.

Multipart bodies parse incrementally off the wire: file parts spool to
temporary files instead of accumulating in RAM, ``max_request_size`` is
enforced chunk-by-chunk, and ``max_request_size`` itself now defaults to
100 MiB instead of unlimited.
"""

import pytest

import responder
from responder import File, Form, UploadFile
from responder.statics import DEFAULT_MAX_REQUEST_SIZE

SPOOL_THRESHOLD = 1024 * 1024  # Starlette rolls SpooledTemporaryFile past 1 MiB.


def _api(**kwargs):
    return responder.API(
        debug=False, allowed_hosts=[";"], session_https_only=False, **kwargs
    )


@pytest.fixture
def api():
    return _api()


@pytest.fixture
def session(api):
    return api.requests


def url(s):
    return f"http://;{s}"


def test_large_file_part_spools_to_disk(api, session):
    payload = b"x" * (3 * SPOOL_THRESHOLD)

    @api.route("/upload", methods=["POST"])
    async def upload(req, resp, *, f: UploadFile = File(...)):
        blob = await f.read()
        resp.media = {
            "size": len(blob),
            "rolled": f.file._rolled,  # SpooledTemporaryFile hit the disk
        }

    r = session.post(url("/upload"), files={"f": ("big.bin", payload)})
    assert r.status_code == 200
    assert r.json() == {"size": len(payload), "rolled": True}


def test_small_file_part_stays_in_memory(api, session):
    @api.route("/upload", methods=["POST"])
    async def upload(req, resp, *, f: UploadFile = File(...)):
        resp.media = {"rolled": f.file._rolled}

    r = session.post(url("/upload"), files={"f": ("small.txt", b"tiny")})
    assert r.json() == {"rolled": False}


def test_form_and_files_share_one_parse(api, session):
    @api.route("/mixed", methods=["POST"])
    async def mixed(req, resp):
        form = await req.media("form")
        files = await req.media("files")
        resp.media = {
            "field": form["name"],
            "files": sorted(files),
            # The file's part must not leak in as a phantom text field.
            "phantom": "f" in form,
        }

    r = session.post(
        url("/mixed"),
        data={"name": "kenneth"},
        files={"f": ("a.txt", b"aaa")},
    )
    assert r.status_code == 200
    assert r.json() == {"field": "kenneth", "files": ["f"], "phantom": False}


def test_raw_body_unavailable_after_streaming_parse(api, session):
    @api.route("/upload", methods=["POST"])
    async def upload(req, resp):
        await req.media("files")
        try:
            await req.content
        except RuntimeError as exc:
            resp.media = {"error": str(exc)}
        else:
            resp.media = {"error": None}

    r = session.post(url("/upload"), files={"f": ("a.txt", b"aaa")})
    assert r.status_code == 200
    assert "already been streamed" in r.json()["error"]


def test_buffered_body_stays_replayable(api, session):
    """Reading req.content first keeps the legacy fully-buffered behavior."""

    @api.route("/upload", methods=["POST"])
    async def upload(req, resp):
        raw = await req.content
        form = await req.media("form")
        files = await req.media("files")
        raw_again = await req.content
        resp.media = {
            "raw_len": len(raw),
            "replayed": raw == raw_again,
            "field": form["name"],
            "files": sorted(files),
        }

    r = session.post(
        url("/upload"), data={"name": "k"}, files={"f": ("a.txt", b"aaa")}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["replayed"] is True
    assert body["field"] == "k"
    assert body["files"] == ["f"]
    assert body["raw_len"] > 0


def test_max_request_size_enforced_mid_stream():
    """An oversized chunked multipart body is rejected while streaming, without
    a Content-Length header to shortcut on."""
    api = _api(max_request_size=10_000)

    @api.route("/upload", methods=["POST"])
    async def upload(req, resp):
        files = await req.media("files")
        resp.media = {"files": sorted(files)}

    def body():
        yield (
            b"--frame\r\n"
            b'Content-Disposition: form-data; name="f"; filename="big.bin"\r\n'
            b"Content-Type: application/octet-stream\r\n\r\n"
        )
        for _ in range(100):
            yield b"a" * 1024
        yield b"\r\n--frame--\r\n"

    r = api.requests.post(
        url("/upload"),
        content=body(),
        headers={"Content-Type": "multipart/form-data; boundary=frame"},
    )
    assert r.status_code == 413


def test_form_marker_reads_streamed_multipart(api, session):
    @api.route("/upload", methods=["POST"])
    async def upload(
        req, resp, *, f: UploadFile = File(...), name: str = Form(...)
    ):
        resp.media = {"name": name, "filename": f.filename}

    r = session.post(
        url("/upload"), data={"name": "widget"}, files={"f": ("a.txt", b"aaa")}
    )
    assert r.status_code == 200
    assert r.json() == {"name": "widget", "filename": "a.txt"}


def test_missing_boundary_is_400(api, session):
    @api.route("/upload", methods=["POST"])
    async def upload(req, resp):
        await req.media("files")
        resp.media = {}

    r = session.post(
        url("/upload"),
        content=b"whatever",
        headers={"Content-Type": "multipart/form-data"},
    )
    assert r.status_code == 400


def test_default_request_size_cap():
    assert DEFAULT_MAX_REQUEST_SIZE == 100 * 1024 * 1024
    assert _api().router.max_request_size == DEFAULT_MAX_REQUEST_SIZE
    assert _api(max_request_size=None).router.max_request_size is None


def test_default_cap_rejects_oversized_declared_body():
    api = _api(max_request_size=1024)

    @api.route("/upload", methods=["POST"])
    async def upload(req, resp):
        resp.media = {"len": len(await req.content)}

    r = api.requests.post(url("/upload"), content=b"x" * 2048)
    assert r.status_code == 413
