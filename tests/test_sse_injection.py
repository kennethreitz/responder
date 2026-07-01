"""SSE frames must not be injectable via newlines in caller-supplied fields.

``event``/``id``/``retry``/comment are single-line fields; a CR/LF there would
terminate the field and let the value inject extra SSE lines or frames. ``data``
must split on any line terminator (\\r\\n, \\r, \\n) into multiple ``data:``
lines rather than pass a lone \\r through.
"""

from starlette.testclient import TestClient

import responder
from responder.models import _format_sse_event, _sse_frame


def _client(api):
    return TestClient(api, base_url="http://;")


def test_newline_in_event_field_is_stripped():
    frame = _sse_frame(data="ok", event="tick\ndata: injected\n\nevent: evil").decode()
    # The event value's newline must not create a new line/frame.
    assert "event: tickdata: injected" in frame
    assert "\ndata: injected" not in frame
    assert "\n\nevent: evil" not in frame


def test_newline_in_id_and_retry_is_stripped():
    frame = _sse_frame(data="x", id="1\nevent: nope", retry="5\ndata: nope").decode()
    assert "id: 1event: nope" in frame
    assert "retry: 5data: nope" in frame
    # Exactly one blank-line frame terminator, so no extra frame was injected.
    assert frame.count("\n\n") == 1


def test_nul_in_id_is_stripped():
    # The SSE spec forbids NUL in the id field.
    frame = _sse_frame(data="x", id="a\x00b").decode()
    assert "id: ab" in frame
    assert "\x00" not in frame


def test_data_splits_on_all_line_terminators():
    frame = _sse_frame(data="a\r\nb\rc\nd").decode()
    assert "data: a\ndata: b\ndata: c\ndata: d" in frame
    # No stray carriage return survives inside a data line.
    assert "\r" not in frame


def test_comment_newline_is_stripped():
    frame = _format_sse_event({"comment": "keep\ndata: injected"}).decode()
    assert frame == ": keepdata: injected\n\n"


def test_end_to_end_event_injection_blocked():
    api = responder.API(allowed_hosts=[";"], session_https_only=False)

    @api.route("/s")
    async def s(req, resp):
        @resp.sse
        async def stream():
            yield {"data": "hello", "event": "tick\ndata: 9999"}

    body = _client(api).get("/s").text
    assert "event: tickdata: 9999" in body
    # The injected "data: 9999" must not appear as its own line.
    assert "\ndata: 9999" not in body
