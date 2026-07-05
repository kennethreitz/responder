"""Tests for the 8.1.0 developer-experience items.

- CLI: `responder run` --host/--port/--server/--reload flags, and bare
  `responder` printing usage instead of silently exiting 0.
- Optional orjson JSON backend (`responder[orjson]`).
- responder.testing: AsyncTestClient, the streaming ASGI transport, and the
  SSE test helpers (parse_sse / iter_sse / collect_sse).
"""

import asyncio
import datetime as dt
import sys

import httpx
import pytest

import responder
from responder.testing import (
    ASGIStreamingTransport,
    AsyncTestClient,
    SSEEvent,
    collect_sse,
    iter_sse,
    parse_sse,
)


@pytest.fixture
def api():
    return responder.API(sessions=False)


# ---------------------------------------------------------------------------
# Item 75: CLI run flags and bare-invocation usage
# ---------------------------------------------------------------------------

pytest.importorskip("docopt", reason="docopt-ng package not installed")

from responder.ext import cli as cli_module  # noqa: E402
from responder.ext.cli import _uvicorn_import_string, cli  # noqa: E402


class _FakeAPI:
    """Records the kwargs `responder run` passes to api.run()."""

    def __init__(self):
        self.run_kwargs = None

    def run(self, **kwargs):
        self.run_kwargs = kwargs


@pytest.fixture
def fake_api(monkeypatch):
    fake = _FakeAPI()
    monkeypatch.setattr(cli_module, "load_target", lambda target: fake)
    return fake


def test_bare_responder_prints_usage_and_exits_nonzero(monkeypatch):
    """Bare `responder` used to exit 0 silently; now it shows usage, exit 1."""
    monkeypatch.setattr(sys, "argv", ["responder"])
    with pytest.raises(SystemExit) as excinfo:
        cli()
    # docopt raises SystemExit with the usage text; a string code means the
    # interpreter prints it to stderr and exits with status 1.
    assert isinstance(excinfo.value.code, str)
    assert "Usage:" in excinfo.value.code
    assert "responder run" in excinfo.value.code


def test_run_flags_are_threaded_to_api_run(monkeypatch, fake_api):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "responder", "run",
            "--host", "0.0.0.0",  # noqa: S104
            "--port", "8123",
            "--server", "uvicorn",
            "app:api",
        ],
    )
    cli()
    assert fake_api.run_kwargs == {
        "debug": False,
        "address": "0.0.0.0",  # noqa: S104
        "port": 8123,
        "server": "uvicorn",
    }


def test_run_without_new_flags_keeps_default_call(monkeypatch, fake_api):
    monkeypatch.setattr(sys, "argv", ["responder", "run", "app:api"])
    cli()
    assert fake_api.run_kwargs == {"debug": False}


def test_run_limit_max_requests_still_threaded(monkeypatch, fake_api):
    monkeypatch.setattr(
        sys, "argv",
        ["responder", "run", "--limit-max-requests", "7", "app:api"],
    )
    cli()
    assert fake_api.run_kwargs == {"debug": False, "limit_max_requests": 7}


def test_run_invalid_port_exits(monkeypatch, fake_api):
    monkeypatch.setattr(
        sys, "argv", ["responder", "run", "--port", "http", "app:api"]
    )
    with pytest.raises(SystemExit) as excinfo:
        cli()
    assert excinfo.value.code == 1
    assert fake_api.run_kwargs is None


def test_run_debug_flag_still_works(monkeypatch, fake_api):
    monkeypatch.setattr(sys, "argv", ["responder", "run", "--debug", "app:api"])
    cli()
    assert fake_api.run_kwargs == {"debug": True}


def test_reload_invokes_uvicorn_with_import_string(monkeypatch, fake_api):
    import uvicorn

    calls = {}

    def fake_run(app, **kwargs):
        calls["app"] = app
        calls["kwargs"] = kwargs

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setattr(
        sys, "argv",
        ["responder", "run", "--reload", "--port", "7001", "acme.app:service"],
    )
    cli()
    # Reload mode hands the *import string* to uvicorn (it must re-import the
    # app in the reloader subprocess); load_target is bypassed.
    assert calls["app"] == "acme.app:service"
    assert calls["kwargs"]["reload"] is True
    assert calls["kwargs"]["port"] == 7001
    assert calls["kwargs"]["host"] == "127.0.0.1"
    assert fake_api.run_kwargs is None


def test_reload_explicit_port_wins_over_port_env(monkeypatch, fake_api):
    import uvicorn

    calls = {}

    def fake_run(app, **kwargs):
        calls["app"] = app
        calls["kwargs"] = kwargs

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setattr(
        sys, "argv",
        ["responder", "run", "--reload", "--port", "7001", "acme.app:service"],
    )
    cli()
    assert calls["app"] == "acme.app:service"
    assert calls["kwargs"]["reload"] is True
    assert calls["kwargs"]["port"] == 7001
    assert calls["kwargs"]["host"] == "0.0.0.0"  # noqa: S104
    assert fake_api.run_kwargs is None


def test_reload_defaults_attribute_to_api(monkeypatch, fake_api):
    import uvicorn

    calls = {}
    monkeypatch.setattr(
        uvicorn, "run", lambda app, **kwargs: calls.setdefault("app", app)
    )
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setattr(sys, "argv", ["responder", "run", "--reload", "acme.app"])
    cli()
    assert calls["app"] == "acme.app:api"


def test_reload_rejects_file_target(monkeypatch, fake_api):
    monkeypatch.setattr(
        sys, "argv", ["responder", "run", "--reload", "examples/helloworld.py"]
    )
    with pytest.raises(SystemExit) as excinfo:
        cli()
    assert excinfo.value.code == 1


def test_run_granian_with_limit_max_requests_is_rejected(monkeypatch, fake_api):
    """granian's embedded server has no request-limit option; the CLI must
    reject the combo with a clear error instead of letting it crash with an
    opaque TypeError inside api.serve (regression, finding 12)."""
    monkeypatch.setattr(
        sys, "argv",
        [
            "responder", "run",
            "--server", "granian",
            "--limit-max-requests", "5",
            "app:api",
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli()
    assert excinfo.value.code == 1
    # It must be rejected before ever reaching api.run().
    assert fake_api.run_kwargs is None


def test_run_uvicorn_still_accepts_limit_max_requests(monkeypatch, fake_api):
    """The granian guard must not block the supported uvicorn combination."""
    monkeypatch.setattr(
        sys, "argv",
        [
            "responder", "run",
            "--server", "uvicorn",
            "--limit-max-requests", "5",
            "app:api",
        ],
    )
    cli()
    assert fake_api.run_kwargs == {
        "debug": False,
        "server": "uvicorn",
        "limit_max_requests": 5,
    }


def test_reload_rejects_granian(monkeypatch, fake_api):
    monkeypatch.setattr(
        sys, "argv",
        ["responder", "run", "--reload", "--server", "granian", "app:api"],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli()
    assert excinfo.value.code == 1


@pytest.mark.parametrize(
    "target,expected",
    [
        ("app", "app:api"),
        ("app:service", "app:service"),
        ("acme.core.app:api", "acme.core.app:api"),
        ("app.py", None),
        ("app.py:api", None),
        ("myapp/core.py:application", None),
        ("https://example.com/app.py", None),
        ("app:not-an-identifier", None),
    ],
)
def test_uvicorn_import_string(target, expected):
    assert _uvicorn_import_string(target) == expected


# ---------------------------------------------------------------------------
# Item 47: optional orjson JSON backend
# ---------------------------------------------------------------------------


def _has_orjson():
    try:
        import orjson  # noqa: F401
    except ImportError:
        return False
    return True


needs_orjson = pytest.mark.skipif(not _has_orjson(), reason="orjson not installed")


@needs_orjson
def test_orjson_encodes_media(api):
    @api.route("/data")
    def data(req, resp):
        resp.media = {"key": "value", "n": [1, 2, 3]}

    r = api.requests.get("/data")
    assert r.headers["Content-Type"] == "application/json"
    # orjson emits compact separators.
    assert r.content == b'{"key":"value","n":[1,2,3]}'
    assert r.json() == {"key": "value", "n": [1, 2, 3]}


@needs_orjson
def test_orjson_non_str_keys_match_stdlib(api):
    @api.route("/keys")
    def keys(req, resp):
        resp.media = {1: "one", 2.5: "half"}

    # stdlib json coerces non-string keys; orjson must do the same.
    assert api.requests.get("/keys").json() == {"1": "one", "2.5": "half"}


@needs_orjson
def test_orjson_dict_subclass_matches_stdlib(api):
    """QueryDict (a dict subclass with overridden items()) must serialize via
    its accessors, like the stdlib encoder — not from its raw list storage."""

    @api.route("/params")
    def params(req, resp):
        resp.media = {"params": req.params}

    r = api.requests.get("/params", params={"q": "q"})
    assert r.json()["params"] == {"q": "q"}


@needs_orjson
def test_orjson_str_and_int_subclasses_match_stdlib(api):
    class UserId(str):
        pass

    class Level(int):
        pass

    @api.route("/subclasses")
    def subclasses(req, resp):
        resp.media = {"uid": UserId("u-1"), "level": Level(3), "flag": True}

    assert api.requests.get("/subclasses").json() == {
        "uid": "u-1",
        "level": 3,
        "flag": True,
    }


@needs_orjson
def test_orjson_builtin_type_conversions(api):
    """datetime/UUID/Decimal/set still route through the default hook."""
    import uuid
    from decimal import Decimal

    when = dt.datetime(2026, 7, 3, 12, 30, 45)
    uid = uuid.UUID("12345678-1234-5678-1234-567812345678")

    @api.route("/types")
    def types(req, resp):
        resp.media = {
            "when": when,
            "day": dt.date(2026, 7, 3),
            "uid": uid,
            "price": Decimal("9.5"),
            "tags": {"a"},
        }

    payload = api.requests.get("/types").json()
    assert payload["when"] == when.isoformat()
    assert payload["day"] == "2026-07-03"
    assert payload["uid"] == str(uid)
    assert payload["price"] == "9.5"
    assert payload["tags"] == ["a"]


@needs_orjson
def test_orjson_custom_encoder_still_works():
    """API(encoder=...) hooks are passed to orjson via default=."""

    class Money:
        def __init__(self, amount):
            self.amount = amount

    def encoder(obj):
        if isinstance(obj, Money):
            return {"amount": obj.amount, "currency": "EUR"}
        raise TypeError(f"unsupported: {obj!r}")

    api = responder.API(sessions=False, encoder=encoder)

    @api.route("/pay")
    def pay(req, resp):
        resp.media = {"total": Money(42)}

    r = api.requests.get("/pay")
    assert r.json() == {"total": {"amount": 42, "currency": "EUR"}}


def test_json_ensure_ascii_true_stays_on_stdlib():
    """orjson is UTF-8-only; the legacy escape mode must keep escaping."""
    api = responder.API(sessions=False, json_ensure_ascii=True)

    @api.route("/unicode")
    def unicode_(req, resp):
        resp.media = {"s": "héllo"}

    r = api.requests.get("/unicode")
    assert b"\\u00e9" in r.content
    assert r.json() == {"s": "héllo"}


def test_json_unicode_passthrough_default(api):
    @api.route("/unicode")
    def unicode_(req, resp):
        resp.media = {"s": "héllo"}

    r = api.requests.get("/unicode")
    assert "héllo".encode() in r.content
    assert r.json() == {"s": "héllo"}


@needs_orjson
def test_orjson_bigint_falls_back_to_stdlib(api):
    """Integers beyond 64 bits serialize fine (stdlib fallback)."""
    big = 2**100

    @api.route("/big")
    def bigview(req, resp):
        resp.media = {"n": big}

    assert api.requests.get("/big").json() == {"n": big}


@pytest.mark.parametrize("disable_orjson", [False, True])
def test_json_decode_bigint_roundtrips_exactly(monkeypatch, disable_orjson):
    """A 30-digit integer in the request body must decode to the exact int,
    not orjson's lossy float. Decoding is stdlib-only (regression, finding 11).
    Verified with and without orjson installed."""
    from responder import formats as formats_module

    if disable_orjson:
        monkeypatch.setattr(formats_module, "_orjson", None)

    big = 10**29 + 7  # 30 digits, far beyond 2**64

    api = responder.API(sessions=False)

    @api.route("/echo", methods=["POST"])
    async def echo(req, resp):
        data = await req.media()
        resp.media = {"n": data["n"], "is_int": isinstance(data["n"], int)}

    r = api.requests.post(
        "/echo",
        content=b'{"n": %d}' % big,
        headers={"Content-Type": "application/json"},
    )
    payload = r.json()
    assert payload["is_int"] is True
    assert payload["n"] == big


@needs_orjson
def test_orjson_str_subclass_ignores_overridden_dunder(api):
    """str subclasses serialize from the *base* value, matching stdlib — an
    overridden __str__ must not leak into the output (regression, finding 13)."""

    class Shouty(str):
        def __str__(self):
            return "OVERRIDDEN"

    @api.route("/s")
    def s(req, resp):
        resp.media = {"v": Shouty("real")}

    assert api.requests.get("/s").json() == {"v": "real"}


@needs_orjson
def test_orjson_int_subclass_ignores_overridden_dunder(api):
    """int subclasses serialize from the *base* value, matching stdlib — an
    overridden __int__ must not leak into the output (regression, finding 13)."""

    class Weird(int):
        def __int__(self):
            return 999

    @api.route("/i")
    def i(req, resp):
        resp.media = {"v": Weird(5)}

    assert api.requests.get("/i").json() == {"v": 5}


def test_json_decode_request_body(api):
    @api.route("/echo", methods=["POST"])
    async def echo(req, resp):
        resp.media = {"got": await req.media()}

    r = api.requests.post("/echo", json={"a": 1, "b": "two"})
    assert r.json() == {"got": {"a": 1, "b": "two"}}


def test_json_decode_invalid_body_is_400(api):
    @api.route("/echo", methods=["POST"])
    async def echo(req, resp):
        resp.media = {"got": await req.media()}

    r = api.requests.post(
        "/echo",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400


def test_json_decode_nan_literal_still_parses(api):
    """orjson rejects NaN literals; the stdlib fallback keeps accepting them."""

    @api.route("/echo", methods=["POST"])
    async def echo(req, resp):
        data = await req.media()
        resp.media = {"is_nan": data["x"] != data["x"]}

    r = api.requests.post(
        "/echo",
        content=b'{"x": NaN}',
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json() == {"is_nan": True}


def test_stdlib_path_when_orjson_unavailable(monkeypatch):
    """Formats built without orjson keep the historic stdlib output."""
    from responder import formats as formats_module

    monkeypatch.setattr(formats_module, "_orjson", None)
    api = responder.API(sessions=False)

    @api.route("/data")
    def data(req, resp):
        resp.media = {"a": 1}

    r = api.requests.get("/data")
    # stdlib json.dumps default separators include a space.
    assert r.content == b'{"a": 1}'
    assert r.json() == {"a": 1}


# ---------------------------------------------------------------------------
# Item 98: AsyncTestClient
# ---------------------------------------------------------------------------


def test_async_client_basic_request(api):
    @api.route("/")
    def home(req, resp):
        resp.text = "hello, world!"

    async def main():
        async with AsyncTestClient(api) as client:
            r = await client.get("/")
            assert r.status_code == 200
            assert r.text == "hello, world!"

    asyncio.run(main())


def test_async_client_uses_streaming_transport(api):
    # Regression for the docs claim (docs/source/testing.rst): AsyncTestClient
    # dispatches over ASGIStreamingTransport, NOT httpx.ASGITransport. The
    # streaming transport is what lets client.stream(...) read endless SSE
    # streams; httpx.ASGITransport buffers the whole body and would deadlock.
    client = AsyncTestClient(api)
    assert isinstance(client._transport, ASGIStreamingTransport)
    assert not isinstance(client._transport, httpx.ASGITransport)


def test_async_client_without_context_manager(api):
    @api.route("/")
    def home(req, resp):
        resp.media = {"ok": True}

    async def main():
        client = AsyncTestClient(api)
        r = await client.get("/")
        assert r.json() == {"ok": True}
        await client.aclose()

    asyncio.run(main())


def test_async_client_runs_lifespan(api):
    events = []

    @api.on_event("startup")
    async def on_startup():
        events.append("startup")

    @api.on_event("shutdown")
    async def on_shutdown():
        events.append("shutdown")

    @api.route("/")
    def home(req, resp):
        resp.media = {"events": list(events)}

    async def main():
        async with AsyncTestClient(api) as client:
            r = await client.get("/")
            assert r.json() == {"events": ["startup"]}
        assert events == ["startup", "shutdown"]

    asyncio.run(main())


def test_async_client_startup_failure_raises(api):
    @api.on_event("startup")
    async def boom():
        raise ValueError("nope")

    async def main():
        with pytest.raises(Exception, match="nope|startup"):
            async with AsyncTestClient(api):
                pytest.fail("must not enter the context")  # pragma: no cover

    asyncio.run(main())


def test_async_client_concurrent_requests(api):
    @api.route("/slow/{n}")
    async def slow(req, resp, *, n):
        await asyncio.sleep(0.05)
        resp.media = {"n": int(n)}

    async def main():
        async with AsyncTestClient(api) as client:
            responses = await asyncio.gather(
                *(client.get(f"/slow/{n}") for n in range(5))
            )
            assert [r.json()["n"] for r in responses] == list(range(5))

    asyncio.run(main())


def test_async_client_raise_app_exceptions_false(api):
    @api.route("/fail")
    def fail(req, resp):
        raise ValueError("something broke")

    async def main():
        client = AsyncTestClient(api, raise_app_exceptions=False)
        r = await client.get("/fail")
        assert r.status_code == 500
        await client.aclose()

    asyncio.run(main())


def test_async_client_propagates_app_exceptions(api):
    @api.route("/fail")
    def fail(req, resp):
        raise ValueError("something broke")

    async def main():
        client = AsyncTestClient(api)
        with pytest.raises(ValueError, match="something broke"):
            await client.get("/fail")
        await client.aclose()

    asyncio.run(main())


def test_async_client_posts_json_body(api):
    @api.route("/echo", methods=["POST"])
    async def echo(req, resp):
        resp.media = {"got": await req.media()}

    async def main():
        async with AsyncTestClient(api) as client:
            r = await client.post("/echo", json={"a": 1})
            assert r.json() == {"got": {"a": 1}}

    asyncio.run(main())


# ---------------------------------------------------------------------------
# Item 98: SSE test helpers
# ---------------------------------------------------------------------------


def test_parse_sse_full_frames():
    text = (
        "event: tick\n"
        "id: 7\n"
        "retry: 1500\n"
        "data: one\n"
        "data: two\n"
        "\n"
        ": heartbeat comment, skipped\n"
        "\n"
        "data: plain\n"
        "\n"
    )
    events = parse_sse(text)
    assert events == [
        SSEEvent(data="one\ntwo", event="tick", id="7", retry=1500),
        SSEEvent(data="plain", event="message", id=None, retry=None),
    ]


def test_parse_sse_defaults_and_edge_cases():
    # No event field -> "message"; value space stripped; invalid retry ignored;
    # unknown fields ignored; unterminated trailing frame still reported.
    events = parse_sse("retry: soon\ndata:no-space\nfancy: ignored\ndata: tail")
    assert events == [
        SSEEvent(data="no-space\ntail", event="message", id=None, retry=None)
    ]
    assert parse_sse("") == []
    assert parse_sse(": only a comment\n\n") == []


def test_sse_event_json_helper():
    assert SSEEvent(data='{"n": 3}').json() == {"n": 3}


def test_parse_sse_from_sync_client(api):
    @api.route("/events")
    async def events(req, resp):
        @resp.sse
        async def stream():
            for n in range(3):
                yield {"data": {"n": n}, "id": n}

    r = api.requests.get("/events")
    assert r.headers["Content-Type"].startswith("text/event-stream")
    parsed = parse_sse(r.text)
    assert len(parsed) == 3
    assert parsed[0].json() == {"n": 0}
    assert parsed[0].id == "0"
    assert parsed[2].json() == {"n": 2}


def test_collect_sse_finite_stream(api):
    @api.route("/events")
    async def events(req, resp):
        @resp.sse
        async def stream():
            yield {"data": {"n": 0}, "event": "tick", "id": "a"}
            yield "plain text"

    async def main():
        async with AsyncTestClient(api) as client:
            r = await client.get("/events")
            events_ = await collect_sse(r)
            assert events_ == [
                SSEEvent(data='{"n": 0}', event="tick", id="a", retry=None),
                SSEEvent(data="plain text", event="message", id=None, retry=None),
            ]

    asyncio.run(main())


def test_iter_sse_endless_stream_with_heartbeat(api):
    """The flagship scenario: read a few events off an endless heartbeat
    stream, then close it — impossible with a buffering ASGI transport."""
    cancelled = asyncio.Event()

    @api.route("/endless")
    async def endless(req, resp):
        @resp.sse(heartbeat=0.01)
        async def stream():
            n = 0
            try:
                while True:
                    yield {"data": {"n": n}, "id": n}
                    n += 1
                    await asyncio.sleep(0.03)
            finally:
                cancelled.set()

    async def main():
        async with AsyncTestClient(api) as client:
            async with client.stream("GET", "/endless") as r:
                assert r.headers["content-type"].startswith("text/event-stream")
                got = []
                async for event in iter_sse(r):
                    got.append(event)
                    if len(got) == 3:
                        break
        assert [e.json()["n"] for e in got] == [0, 1, 2]
        # Closing the response disconnects the producer.
        await asyncio.wait_for(cancelled.wait(), timeout=2)

    asyncio.run(main())


def test_iter_sse_skips_heartbeat_comments(api):
    @api.route("/beats")
    async def beats(req, resp):
        @resp.sse(heartbeat=0.01)
        async def stream():
            yield {"data": "first"}
            await asyncio.sleep(0.05)  # let a heartbeat comment through
            yield {"data": "second"}

    async def main():
        async with AsyncTestClient(api) as client:
            r = await client.get("/beats")
            events = await collect_sse(r)
            assert [e.data for e in events] == ["first", "second"]

    asyncio.run(main())
