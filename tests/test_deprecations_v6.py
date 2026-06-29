"""v5.6: 6.0 deprecation staging — json_ensure_ascii knob + files warning."""

import pytest
from starlette.testclient import TestClient

import responder


def _client(api):
    return TestClient(api, base_url="http://;")


def test_json_ensure_ascii_default_escapes():
    api = responder.API(allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False)

    @api.route("/j")
    def j(req, resp):
        resp.media = {"msg": "wörld"}

    body = _client(api).get("/j").content
    assert b"\\u" in body  # escaped by default
    assert "wörld".encode() not in body


def test_json_ensure_ascii_false_emits_utf8():
    api = responder.API(
        allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False,
        json_ensure_ascii=False,
    )

    @api.route("/j")
    def j(req, resp):
        resp.media = {"msg": "wörld"}

    body = _client(api).get("/j").content
    assert "wörld".encode() in body
    assert b"\\u" not in body


def test_media_files_warns_but_still_returns_bytes_dict():
    api = responder.API(allowed_hosts=[";"], secret_key="x" * 32, session_https_only=False)

    @api.route("/u", methods=["POST"])
    async def u(req, resp):
        with pytest.warns(DeprecationWarning, match="UploadFile"):
            files = await req.media("files")
        resp.media = {"type": type(files["doc"]).__name__, "keys": sorted(files)}

    r = _client(api).post(
        "/u", files={"doc": ("x.bin", b"\x00\x01", "application/octet-stream")}
    )
    assert r.json() == {"type": "dict", "keys": ["doc"]}
