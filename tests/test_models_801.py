"""Regression tests for the 8.0.1 models.py fixes:

- [31] apparent_encoding: UTF-8 fast path; chardet off the event loop
- [33] stream_file/file send Content-Length; HEAD on resp.file() skips the read
- [37] resp.problem() serializes through the type-aware JSON encoder
- [38] QueryDict item assignment / update() no longer corrupt scalar values
- [40] resp.stream()/resp.sse() raise TypeError instead of bare assert
- [30] accepts(): an explicit q=0 range excludes the type (most-specific wins)
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

import responder
from responder.models import QueryDict, Request


def _request_with_accept(accept):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"accept", accept.encode("latin-1"))],
        "query_string": b"",
    }

    async def receive():
        return {"type": "http.request", "body": b""}

    return Request(scope, receive)


# ---------------------------------------------------------------------------
# [38] QueryDict mutation


def test_querydict_setitem_scalar_roundtrips():
    qd = QueryDict("")
    qd["x"] = "hello"
    assert qd["x"] == "hello"
    assert qd.get_list("x") == ["hello"]


def test_querydict_setitem_list_kept_as_is():
    qd = QueryDict("")
    qd["x"] = ["a", "b"]
    assert qd["x"] == "b"
    assert qd.get_list("x") == ["a", "b"]


def test_querydict_update_scalar_roundtrips():
    qd = QueryDict("a=1")
    qd.update({"y": "world"}, z="zed")
    assert qd["y"] == "world"
    assert qd["z"] == "zed"
    assert qd["a"] == "1"  # parse_qs lists still stored intact


def test_querydict_update_pairs_and_setdefault():
    qd = QueryDict("")
    qd.update([("k", "v")])
    assert qd["k"] == "v"
    assert qd.setdefault("k", "other") == "v"
    assert qd.setdefault("new", "fresh") == "fresh"
    assert qd.get_list("new") == ["fresh"]


def test_querydict_update_from_querydict_preserves_multivalues():
    # v8.0.0's dict.update C fast path copied the stored lists; the override
    # must not collapse ["1", "2"] to ["2"] via QueryDict.items().
    src = QueryDict("x=1&x=2&y=only")
    dst = QueryDict("x=0")
    dst.update(src)
    assert dst.get_list("x") == ["1", "2"]
    assert dst["x"] == "2"
    assert dst.get_list("y") == ["only"]


def test_querydict_parsing_unchanged():
    qd = QueryDict("multi=1&multi=2&empty=")
    assert qd["multi"] == "2"
    assert qd.get_list("multi") == ["1", "2"]
    assert qd.get("empty") == ""  # keep_blank_values: blank stays present


# ---------------------------------------------------------------------------
# [30] accepts() specificity / q=0 exclusions


def test_accepts_q0_exact_beats_wildcard():
    req = _request_with_accept("text/html;q=0, */*")
    assert req.accepts("text/html") is False
    assert req.accepts("application/json") is True


def test_accepts_q0_type_wildcard_beats_star_star():
    req = _request_with_accept("application/*;q=0, */*")
    assert req.accepts("application/json") is False
    assert req.accepts("text/html") is True


def test_accepts_exact_overrides_q0_wildcard():
    # The exact range is the most specific match and it is acceptable.
    req = _request_with_accept("text/html;q=0.5, text/*;q=0")
    assert req.accepts("text/html") is True
    assert req.accepts("text/plain") is False


def test_accepts_bare_token_q0_exclusion():
    req = _request_with_accept("application/json;q=0, */*")
    assert req.accepts("json") is False
    assert req.accepts("yaml") is True


def test_accepts_bare_token_unrelated_type_wildcard_q0_does_not_veto():
    # An audio/*;q=0 range says nothing about a bare "json" token; */* still
    # accepts it. Only the q=0 wildcard alone leaves nothing acceptable.
    req = _request_with_accept("audio/*;q=0, */*")
    assert req.accepts("json") is True
    assert req.accepts("yaml") is True
    assert _request_with_accept("audio/*;q=0").accepts("json") is False


def test_accepts_existing_behavior_unchanged():
    assert _request_with_accept("*/*").accepts("application/json") is True
    assert _request_with_accept("application/*").accepts("application/json") is True
    assert _request_with_accept("text/html").accepts("application/json") is False
    assert (
        _request_with_accept("application/json;q=0, text/html").accepts(
            "application/json"
        )
        is False
    )


# ---------------------------------------------------------------------------
# [31] apparent_encoding


def test_apparent_encoding_utf8_fast_path(api):
    @api.route("/", methods=["POST"])
    async def view(req, resp):
        resp.text = await req.apparent_encoding

    # Valid UTF-8, no charset= declared: recognized without chardet guessing.
    r = api.requests.post("http://;/", content="héllo ünïcode".encode())
    assert r.text == "utf-8"


def test_apparent_encoding_non_utf8_still_detected(api):
    @api.route("/", methods=["POST"])
    async def view(req, resp):
        body = await req.text  # decodes via detected encoding
        resp.media = {"text": body}

    # latin-1 bytes that are invalid UTF-8: falls back to detection.
    r = api.requests.post("http://;/", content="café".encode("latin-1"))
    assert r.status_code == 200
    assert r.json()["text"] == "café"


def test_apparent_encoding_declared_charset_wins(api):
    @api.route("/", methods=["POST"])
    async def view(req, resp):
        resp.text = await req.apparent_encoding

    r = api.requests.post(
        "http://;/",
        content=b"hello",
        headers={"Content-Type": "text/plain; charset=iso-8859-1"},
    )
    assert r.text == "iso-8859-1"


# ---------------------------------------------------------------------------
# [33] Content-Length on file responses; HEAD skips the deferred read


@pytest.fixture
def bigfile(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"0123456789" * 100)  # 1000 bytes
    return path


def test_stream_file_sends_content_length(api, bigfile):
    @api.route("/f")
    async def view(req, resp):
        resp.stream_file(str(bigfile))

    r = api.requests.get("http://;/f", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200
    assert r.headers["Content-Length"] == "1000"
    assert r.content == bigfile.read_bytes()


def test_stream_file_range_content_length(api, bigfile):
    @api.route("/f")
    async def view(req, resp):
        resp.stream_file(str(bigfile))

    r = api.requests.get(
        "http://;/f", headers={"Range": "bytes=0-99", "Accept-Encoding": "identity"}
    )
    assert r.status_code == 206
    assert r.headers["Content-Length"] == "100"
    assert len(r.content) == 100


def test_stream_file_multipart_range_content_length(api, bigfile):
    @api.route("/f")
    async def view(req, resp):
        resp.stream_file(str(bigfile))

    r = api.requests.get(
        "http://;/f",
        headers={"Range": "bytes=0-9,500-509", "Accept-Encoding": "identity"},
    )
    assert r.status_code == 206
    assert r.headers["Content-Length"] == str(len(r.content))


def test_stream_file_head_reports_size(api, bigfile):
    @api.route("/f")
    async def view(req, resp):
        resp.stream_file(str(bigfile))

    r = api.requests.head("http://;/f", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200
    assert r.headers["Content-Length"] == "1000"
    assert r.content == b""


def test_download_sends_content_length(api, bigfile):
    @api.route("/d")
    async def view(req, resp):
        resp.download(str(bigfile), filename="data.bin")

    r = api.requests.get("http://;/d", headers={"Accept-Encoding": "identity"})
    assert r.headers["Content-Length"] == "1000"
    assert "attachment" in r.headers["Content-Disposition"]


def test_file_head_skips_read_and_reports_size(api, bigfile, monkeypatch):
    read_calls = []
    original_open = type(bigfile).open

    def counting_open(self, *args, **kwargs):
        read_calls.append(args)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(type(bigfile), "open", counting_open)

    @api.route("/f")
    async def view(req, resp):
        resp.file(str(bigfile))

    r = api.requests.head("http://;/f", headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200
    assert r.headers["Content-Length"] == "1000"
    assert r.content == b""
    assert read_calls == []  # the file was never opened for a HEAD

    # A GET still serves the full body (and does open the file).
    r = api.requests.get("http://;/f", headers={"Accept-Encoding": "identity"})
    assert r.content == bigfile.read_bytes()
    assert r.headers["Content-Length"] == "1000"
    assert read_calls


def test_file_get_range_content_length(api, bigfile):
    @api.route("/f")
    async def view(req, resp):
        resp.file(str(bigfile))

    r = api.requests.get(
        "http://;/f", headers={"Range": "bytes=10-19", "Accept-Encoding": "identity"}
    )
    assert r.status_code == 206
    assert r.headers["Content-Length"] == "10"
    assert r.content == b"0123456789"


def test_text_after_file_clears_stale_content_length(api, bigfile):
    @api.route("/swap")
    async def view(req, resp):
        resp.file(str(bigfile))
        resp.text = "tiny"  # replaces the body; framing must follow

    r = api.requests.get("http://;/swap", headers={"Accept-Encoding": "identity"})
    assert r.content == b"tiny"
    assert r.headers["Content-Length"] == "4"


def test_text_after_stream_file_clears_stale_content_length(api, bigfile):
    @api.route("/swap")
    async def view(req, resp):
        resp.stream_file(str(bigfile))
        resp.text = "tiny"

    r = api.requests.get("http://;/swap", headers={"Accept-Encoding": "identity"})
    assert r.content == b"tiny"
    assert r.headers["Content-Length"] == "4"


def test_problem_after_file_clears_range_headers(api, bigfile):
    @api.route("/oops")
    async def view(req, resp):
        resp.file(str(bigfile))
        resp.problem(404, detail="gone")  # error branch after scheduling a file

    r = api.requests.get(
        "http://;/oops",
        headers={"Range": "bytes=0-9", "Accept-Encoding": "identity"},
    )
    assert r.status_code == 404
    assert r.headers["Content-Length"] == str(len(r.content))
    assert "Content-Range" not in r.headers
    assert "Accept-Ranges" not in r.headers


def test_file_conditional_304_still_works(api, bigfile):
    @api.route("/f")
    async def view(req, resp):
        resp.file(str(bigfile))

    first = api.requests.get("http://;/f")
    etag = first.headers["ETag"]
    r = api.requests.get("http://;/f", headers={"If-None-Match": etag})
    assert r.status_code == 304


# ---------------------------------------------------------------------------
# [37] resp.problem() type-aware serialization


def test_problem_serializes_datetime_and_uuid(api):
    moment = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)
    uid = UUID("12345678-1234-5678-1234-567812345678")

    @api.route("/oops")
    async def view(req, resp):
        resp.problem(409, detail="conflict", occurred_at=moment, ref=uid)

    r = api.requests.get("http://;/oops")
    assert r.status_code == 409
    assert r.headers["Content-Type"].startswith("application/problem+json")
    data = r.json()
    assert data["detail"] == "conflict"
    assert data["occurred_at"] == moment.isoformat()
    assert data["ref"] == str(uid)


def test_problem_honors_custom_api_encoder():
    class Money:
        def __init__(self, amount):
            self.amount = amount

    api = responder.API(
        allowed_hosts=[";"],
        session_https_only=False,
        encoder=lambda obj: (
            f"${obj.amount}" if isinstance(obj, Money) else (_ for _ in ()).throw(
                TypeError(f"cannot encode {obj!r}")
            )
        ),
    )

    @api.route("/oops")
    async def view(req, resp):
        resp.problem(402, detail="pay up", amount=Money(42))

    r = api.requests.get("http://;/oops")
    assert r.status_code == 402
    assert r.json()["amount"] == "$42"


# ---------------------------------------------------------------------------
# [40] stream/sse TypeError


def test_stream_rejects_sync_generator(api):
    @api.route("/s")
    async def view(req, resp):
        def not_async():
            yield b"x"

        with pytest.raises(TypeError, match="async generator"):
            resp.stream(not_async)
        resp.media = {"ok": True}

    assert api.requests.get("http://;/s").json() == {"ok": True}


def test_sse_rejects_sync_generator():
    api = responder.API(allowed_hosts=[";"], session_https_only=False)

    @api.route("/e")
    async def view(req, resp):
        def not_async():
            yield {"data": "x"}

        with pytest.raises(TypeError, match="async generator"):
            resp.sse(not_async)
        with pytest.raises(TypeError, match="async generator"):
            resp.sse(heartbeat=1)(not_async)
        resp.media = {"ok": True}

    assert api.requests.get("http://;/e").json() == {"ok": True}


def test_stream_still_accepts_async_generator(api):
    @api.route("/s")
    async def view(req, resp):
        @resp.stream
        async def body():
            for i in range(3):
                yield f"chunk{i}".encode()

    r = api.requests.get("http://;/s")
    assert r.content == b"chunk0chunk1chunk2"
