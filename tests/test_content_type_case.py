"""Content-Type matching is case-insensitive (RFC 7231 §3.1.1.1).

A client (or intermediary proxy) may send ``Application/JSON`` or
``Multipart/Form-Data``; the framework must still negotiate and parse the body
rather than silently falling through to the wrong parser.
"""

import json

from starlette.testclient import TestClient

import responder


def _client(api):
    return TestClient(api, base_url="http://;")


def _api():
    return responder.API(allowed_hosts=[";"], session_https_only=False)


def test_uppercase_json_content_type_is_parsed():
    api = _api()

    @api.route("/echo", methods=["POST"])
    async def echo(req, resp):
        resp.media = {"got": await req.media(), "is_json": req.is_json}

    r = _client(api).post(
        "/echo",
        content=json.dumps({"a": 1}),
        headers={"Content-Type": "Application/JSON"},
    )
    assert r.status_code == 200
    assert r.json() == {"got": {"a": 1}, "is_json": True}


def test_uppercase_urlencoded_content_type_is_parsed():
    api = _api()

    @api.route("/form", methods=["POST"])
    async def form(req, resp):
        data = await req.media()
        resp.media = {"name": data.get("name")}

    r = _client(api).post(
        "/form",
        content="name=widget",
        headers={"Content-Type": "Application/X-WWW-Form-Urlencoded"},
    )
    assert r.status_code == 200
    assert r.json() == {"name": "widget"}


def test_uppercase_multipart_content_type_is_parsed():
    api = _api()

    @api.route("/multipart", methods=["POST"])
    async def multipart(req, resp):
        data = await req.media("form")
        resp.media = {"name": data.get("name")}

    boundary = "MyBoundaryXYZ"  # mixed case must be preserved in the value
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="name"\r\n'
        "\r\n"
        "widget\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    r = _client(api).post(
        "/multipart",
        content=body,
        # Both the type token and the ``Boundary`` parameter name are uppercased.
        headers={"Content-Type": f"Multipart/Form-Data; Boundary={boundary}"},
    )
    assert r.status_code == 200
    assert r.json() == {"name": "widget"}
