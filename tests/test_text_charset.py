"""Text responses declare charset= in Content-Type and honor resp.encoding."""


def test_text_response_declares_charset(api):
    @api.route("/")
    def view(req, resp):
        resp.text = "hello"

    r = api.requests.get("/")
    assert r.headers["Content-Type"] == "text/plain; charset=utf-8"
    assert r.content == b"hello"


def test_html_response_declares_charset(api):
    @api.route("/")
    def view(req, resp):
        resp.html = "<h1>hi</h1>"

    r = api.requests.get("/")
    assert r.headers["Content-Type"] == "text/html; charset=utf-8"


def test_no_nonstandard_encoding_header(api):
    @api.route("/")
    def view(req, resp):
        resp.text = "hello"

    r = api.requests.get("/")
    assert "Encoding" not in r.headers


def test_html_honors_resp_encoding(api):
    # resp.encoding used to be ignored for text/html: the str body fell
    # through to Starlette's UTF-8 encode (mojibake for latin-1).
    @api.route("/")
    def view(req, resp):
        resp.html = "<p>café</p>"
        resp.encoding = "latin-1"

    r = api.requests.get("/")
    assert r.headers["Content-Type"] == "text/html; charset=latin-1"
    assert r.content == "<p>café</p>".encode("latin-1")
    assert r.text == "<p>café</p>"  # client decodes via the declared charset


def test_text_honors_resp_encoding(api):
    @api.route("/")
    def view(req, resp):
        resp.text = "café"
        resp.encoding = "latin-1"

    r = api.requests.get("/")
    assert r.headers["Content-Type"] == "text/plain; charset=latin-1"
    assert r.content == "café".encode("latin-1")


def test_explicit_charset_in_mimetype_is_not_doubled(api):
    @api.route("/")
    def view(req, resp):
        resp.text = "hello"
        resp.mimetype = "text/plain; charset=UTF-8"

    r = api.requests.get("/")
    assert r.headers["Content-Type"].lower().count("charset=") == 1


def test_bytes_body_with_text_type_gets_no_charset(api):
    # A raw-bytes body has an unknown encoding; appending charset=utf-8
    # would make clients mojibake e.g. Latin-1 content. 7.x sent the bare
    # media type and let clients sniff.
    @api.route("/")
    def view(req, resp):
        resp.content = "<p>café</p>".encode("latin-1")
        resp.mimetype = "text/html"

    r = api.requests.get("/")
    assert r.headers["Content-Type"] == "text/html"
    assert r.content == "<p>café</p>".encode("latin-1")


def test_file_of_html_gets_no_charset(api, tmp_path):
    page = tmp_path / "page.html"
    page.write_bytes("<p>café</p>".encode("latin-1"))

    @api.route("/")
    def view(req, resp):
        resp.file(page)

    r = api.requests.get("/")
    assert r.headers["Content-Type"] == "text/html"
    assert r.content == "<p>café</p>".encode("latin-1")


def test_bytes_body_keeps_user_explicit_charset(api):
    @api.route("/")
    def view(req, resp):
        resp.content = "<p>café</p>".encode("latin-1")
        resp.mimetype = "text/html; charset=latin-1"

    r = api.requests.get("/")
    assert r.headers["Content-Type"] == "text/html; charset=latin-1"
    assert r.text == "<p>café</p>"


def test_non_text_content_type_gets_no_charset(api):
    @api.route("/")
    def view(req, resp):
        resp.content = b"\x00\x01"
        resp.mimetype = "application/octet-stream"

    r = api.requests.get("/")
    assert r.headers["Content-Type"] == "application/octet-stream"
