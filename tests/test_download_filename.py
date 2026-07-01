"""resp.download() escapes user-derived filenames in Content-Disposition."""

import pytest


@pytest.fixture
def download_route(api, tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"x")

    def build(filename):
        @api.route("/dl")
        def dl(req, resp):
            resp.download(f, filename=filename)

        return api.requests.get("/dl")

    return build


def test_quote_in_filename_is_escaped(download_route):
    r = download_route('evil".txt')
    disposition = r.headers["Content-Disposition"]
    assert disposition == (
        'attachment; filename="evil\\".txt"; filename*=UTF-8\'\'evil%22.txt'
    )


def test_backslash_in_filename_is_escaped(download_route):
    r = download_route("back\\slash.txt")
    disposition = r.headers["Content-Disposition"]
    assert disposition == (
        'attachment; filename="back\\\\slash.txt"; '
        "filename*=UTF-8''back%5Cslash.txt"
    )


def test_crlf_in_filename_cannot_inject_headers(download_route):
    r = download_route("a\r\nSet-Cookie: pwned=1\r\n.txt")
    disposition = r.headers["Content-Disposition"]
    assert "\r" not in disposition
    assert "\n" not in disposition
    assert "set-cookie" not in r.headers
    assert "pwned" in disposition  # sanitized into the value, not a new header


def test_nul_is_stripped(download_route):
    r = download_route("nul\x00led.txt")
    assert "\x00" not in r.headers["Content-Disposition"]


def test_token_filename_stays_plain_quoted(download_route):
    r = download_route("report.csv")
    assert r.headers["Content-Disposition"] == 'attachment; filename="report.csv"'


def test_filename_with_space_gets_rfc5987_form_too(download_route):
    r = download_route("annual report.pdf")
    assert r.headers["Content-Disposition"] == (
        'attachment; filename="annual report.pdf"; '
        "filename*=UTF-8''annual%20report.pdf"
    )


def test_unicode_filename_uses_rfc5987_only(download_route):
    r = download_route("résumé.pdf")
    assert r.headers["Content-Disposition"] == (
        "attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf"
    )
