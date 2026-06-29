"""v6: JSON UTF-8 default + req.media('files') returns UploadFile objects."""

from starlette.testclient import TestClient

import responder
from responder import UploadFile


def _client(api):
    return TestClient(api, base_url="http://;")


def test_json_emits_utf8_by_default():
    # v6: ensure_ascii defaults to False.
    api = responder.API(allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False)

    @api.route("/j")
    def j(req, resp):
        resp.media = {"msg": "wörld"}

    body = _client(api).get("/j").content
    assert "wörld".encode() in body
    assert b"\\u" not in body


def test_json_ensure_ascii_opt_in_escapes():
    api = responder.API(
        allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False,
        json_ensure_ascii=True,
    )

    @api.route("/j")
    def j(req, resp):
        resp.media = {"msg": "wörld"}

    body = _client(api).get("/j").content
    assert b"\\u" in body
    assert "wörld".encode() not in body


def test_media_files_returns_uploadfile():
    # v6: the bytes-dict contract is replaced by streaming UploadFile objects.
    api = responder.API(allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False)

    @api.route("/u", methods=["POST"])
    async def u(req, resp):
        files = await req.media("files")
        f = files["doc"]
        assert isinstance(f, UploadFile)
        resp.media = {"name": f.filename, "content": (await f.read()).decode()}

    r = _client(api).post("/u", files={"doc": ("x.txt", b"hi", "text/plain")})
    assert r.json() == {"name": "x.txt", "content": "hi"}
