"""Content-negotiation & decoding fixes:

- accepts() honors media ranges (*/*, type/*) and q-values
- media() auto-detects msgpack
- request body decoding honors the Content-Type charset= parameter
"""

import msgpack
from starlette.testclient import TestClient

import responder


def _client(api):
    return TestClient(api, base_url="http://;")


def _accepts_api():
    api = responder.API(allowed_hosts=[";"], session_https_only=False)

    @api.route("/a")
    def view(req, resp):
        resp.media = {
            "full": req.accepts("application/json"),
            "token": req.accepts("json"),
        }

    return api


def test_accepts_wildcard_range():
    r = _client(_accepts_api()).get("/a", headers={"Accept": "*/*"})
    assert r.json() == {"full": True, "token": True}


def test_accepts_type_wildcard():
    r = _client(_accepts_api()).get("/a", headers={"Accept": "application/*"})
    assert r.json()["full"] is True


def test_accepts_exact_and_qvalue_zero():
    api = _accepts_api()
    assert _client(api).get("/a", headers={"Accept": "application/json"}).json()["full"]
    # q=0 means "not acceptable".
    r = _client(api).get(
        "/a", headers={"Accept": "application/json;q=0, text/html"}
    )
    assert r.json()["full"] is False


def test_accepts_absent_header_accepts_anything():
    # Starlette TestClient sends a default Accept of */* when omitted, so send
    # an explicit empty header to exercise the "absent" branch.
    r = _client(_accepts_api()).get("/a", headers={"Accept": ""})
    assert r.json() == {"full": True, "token": True}


def test_accepts_non_matching_type():
    r = _client(_accepts_api()).get("/a", headers={"Accept": "text/html"})
    assert r.json() == {"full": False, "token": False}


def test_media_autodetects_msgpack():
    api = responder.API(allowed_hosts=[";"], session_https_only=False)

    @api.route("/m", methods=["POST"])
    async def view(req, resp):
        resp.media = {"got": await req.media()}

    body = msgpack.packb({"x": 1, "y": [1, 2, 3]})
    r = _client(api).post(
        "/m", content=body, headers={"Content-Type": "application/x-msgpack"}
    )
    assert r.status_code == 200
    assert r.json() == {"got": {"x": 1, "y": [1, 2, 3]}}


def test_request_body_honors_declared_charset():
    api = responder.API(allowed_hosts=[";"], session_https_only=False)

    @api.route("/t", methods=["POST"])
    async def view(req, resp):
        resp.media = {"text": await req.text}

    # "café" encoded as latin-1; a UTF-8 or chardet guess would mangle it.
    r = _client(api).post(
        "/t",
        content="café".encode("latin-1"),
        headers={"Content-Type": "text/plain; charset=iso-8859-1"},
    )
    assert r.status_code == 200
    assert r.json() == {"text": "café"}
