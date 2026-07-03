"""Response headers and access logs are scrubbed of CR/LF/NUL so attacker-
controlled values (a user-supplied redirect target, a reflected header, a
decoded request path) cannot inject headers, split the response, or forge log
records."""


def test_redirect_location_strips_crlf(api):
    @api.route("/go")
    def go(req, resp):
        resp.redirect("/next\r\nX-Injected: pwned", allow_external=False)

    r = api.requests.get("/go", follow_redirects=False)
    assert r.status_code == 307
    assert "\r" not in r.headers["Location"] and "\n" not in r.headers["Location"]
    assert r.headers.get("x-injected") is None


def test_reflected_header_value_strips_crlf(api):
    @api.route("/hdr")
    def hdr(req, resp):
        resp.headers["X-Custom"] = "val\r\nX-Evil: injected"
        resp.text = "ok"

    r = api.requests.get("/hdr")
    assert r.headers["x-custom"] == "valX-Evil: injected"
    assert r.headers.get("x-evil") is None


def test_created_location_strips_crlf(api):
    @api.route("/thing")
    def thing(req, resp):
        resp.created({"ok": True}, location="/items/1\r\nSet-Cookie: a=b")

    r = api.requests.get("/thing")
    assert r.status_code == 201
    assert "\r" not in r.headers["Location"] and "\n" not in r.headers["Location"]
    # The forged Set-Cookie must not have materialized as its own header.
    assert "a=b" not in (r.headers.get("set-cookie") or "")


def test_nul_byte_stripped_from_header(api):
    @api.route("/nul")
    def nul(req, resp):
        resp.headers["X-Thing"] = "a\x00b"
        resp.text = "ok"

    r = api.requests.get("/nul")
    assert r.headers["x-thing"] == "ab"


def test_scrub_header_value_helper():
    from responder.models import _scrub_header_value

    assert _scrub_header_value("a\r\nb\x00c") == "abc"
    # Non-string values pass through untouched (Starlette rejects them later).
    assert _scrub_header_value(123) == 123


def test_access_log_path_scrubbed_of_control_bytes():
    from responder.ext.logging import _scrub_log_field

    # Control bytes are stripped, legitimate content preserved.
    assert _scrub_log_field("/foo\r\n[INFO] fake") == "/foo[INFO] fake"
    assert _scrub_log_field("/path with space/ünïcode") == "/path with space/ünïcode"
    assert _scrub_log_field("/x\ttab\x7fdel") == "/xtabdel"
