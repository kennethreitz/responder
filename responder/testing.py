"""Small testing helpers for Responder applications."""

from __future__ import annotations

import asyncio
import contextlib
import json as _json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import TracebackType
from typing import Any

import httpx

__all__ = [
    "ASGIStreamingTransport",
    "AsyncTestClient",
    "SSEEvent",
    "assert_problem",
    "collect_sse",
    "iter_sse",
    "parse_sse",
]


def assert_problem(
    response: Any, status: int | None = None, **expected: Any
) -> dict:
    """Assert that ``response`` is an ``application/problem+json`` response.

    Returns the decoded payload so tests can make additional assertions.
    """
    if status is not None:
        assert response.status_code == status
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("application/problem+json")
    payload = response.json()
    assert payload["type"]
    assert isinstance(payload["title"], str)
    assert payload["status"] == response.status_code
    for key, value in expected.items():
        assert payload[key] == value
    return payload


class _Lifespan:
    """Drive an ASGI application's lifespan protocol (startup/shutdown)."""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._to_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._from_app: asyncio.Queue[Any] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}}
        try:
            await self._app(scope, self._to_app.get, self._from_app.put)
        except BaseException as exc:  # surface app crashes to the test
            await self._from_app.put(exc)

    async def _receive(self) -> Any:
        message = await self._from_app.get()
        if isinstance(message, BaseException):
            raise message
        return message

    async def startup(self) -> None:
        """Send ``lifespan.startup`` and wait for the app to complete it."""
        self._task = asyncio.ensure_future(self._run())
        await self._to_app.put({"type": "lifespan.startup"})
        message = await self._receive()
        if message["type"] == "lifespan.startup.failed":
            raise RuntimeError(message.get("message") or "Lifespan startup failed")

    async def shutdown(self) -> None:
        """Send ``lifespan.shutdown`` and wait for the app to complete it."""
        task, self._task = self._task, None
        if task is None:
            return
        await self._to_app.put({"type": "lifespan.shutdown"})
        try:
            message = await self._receive()
        except BaseException:
            task.cancel()
            raise
        if message["type"] == "lifespan.shutdown.failed":
            raise RuntimeError(message.get("message") or "Lifespan shutdown failed")
        await task


_DONE = object()  # sentinel: the response body is complete


class _StreamingByteStream(httpx.AsyncByteStream):
    """Response byte stream fed live from a running ASGI application task."""

    def __init__(
        self,
        chunks: asyncio.Queue[Any],
        task: asyncio.Task[None],
        disconnect: asyncio.Event,
        raise_app_exceptions: bool,
    ) -> None:
        self._chunks = chunks
        self._task = task
        self._disconnect = disconnect
        self._raise_app_exceptions = raise_app_exceptions

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._chunks.get()
            if item is _DONE:
                if self._raise_app_exceptions:
                    # Match httpx.ASGITransport: an unhandled application
                    # exception wins even when a complete response body was
                    # already sent (Starlette's error middleware renders the
                    # 500 first, then re-raises).
                    await self._task
                    while not self._chunks.empty():
                        leftover = self._chunks.get_nowait()
                        if isinstance(leftover, BaseException):
                            raise leftover
                return
            if isinstance(item, BaseException):
                if self._raise_app_exceptions:
                    raise item
                return
            yield item

    async def aclose(self) -> None:
        # Signal http.disconnect to the app, then cancel whatever remains of
        # it — this is what lets tests stop reading an endless stream.
        self._disconnect.set()
        self._task.cancel()
        with contextlib.suppress(BaseException):
            await self._task


class ASGIStreamingTransport(httpx.AsyncBaseTransport):
    """An ASGI transport that streams response bodies as the app produces them.

    ``httpx.ASGITransport`` runs the application to completion and buffers the
    entire body before returning the response — fine for regular endpoints, a
    deadlock for endless streams such as SSE with heartbeats. This transport
    returns as soon as the application starts the response: body chunks are
    yielded live while the handler keeps running, and closing the response
    disconnects (and cancels) the handler.

    This is the transport behind :class:`AsyncTestClient`. It is
    asyncio-only.

    :param app: The ASGI application under test.
    :param raise_app_exceptions: If ``True`` (default), unhandled application
        exceptions propagate to the caller instead of rendering a 500 or a
        truncated body.
    """

    def __init__(self, app: Any, raise_app_exceptions: bool = True) -> None:
        self.app = app
        self.raise_app_exceptions = raise_app_exceptions

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert isinstance(request.stream, httpx.AsyncByteStream)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "headers": [(k.lower(), v) for (k, v) in request.headers.raw],
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "server": (request.url.host, request.url.port),
            "client": ("testclient", 50000),
            "root_path": "",
        }

        request_body_chunks = request.stream.__aiter__()
        request_complete = False
        disconnect = asyncio.Event()

        response_started = asyncio.Event()
        status_code: int | None = None
        response_headers: list[tuple[bytes, bytes]] | None = None
        chunks: asyncio.Queue[Any] = asyncio.Queue()

        async def receive() -> dict[str, Any]:
            nonlocal request_complete
            if request_complete:
                # Block until the client closes the response, then report the
                # disconnect (mirrors a real server; StreamingResponse's
                # disconnect listener relies on this blocking).
                await disconnect.wait()
                return {"type": "http.disconnect"}
            try:
                body = await request_body_chunks.__anext__()
            except StopAsyncIteration:
                request_complete = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.request", "body": body, "more_body": True}

        async def send(message: dict[str, Any]) -> None:
            nonlocal status_code, response_headers
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = message.get("headers", [])
                response_started.set()
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body and request.method != "HEAD":
                    await chunks.put(body)
                if not message.get("more_body", False):
                    await chunks.put(_DONE)

        async def run_app() -> None:
            nonlocal status_code, response_headers
            try:
                await self.app(scope, receive, send)
            except Exception as exc:
                if self.raise_app_exceptions:
                    await chunks.put(exc)
                else:
                    if status_code is None:
                        status_code = 500
                    if response_headers is None:
                        response_headers = []
                    await chunks.put(_DONE)
            else:
                # Safety net for apps that return without a final
                # ``more_body: False`` message.
                await chunks.put(_DONE)
            response_started.set()

        task = asyncio.ensure_future(run_app())
        await response_started.wait()

        if status_code is None:
            # The app finished (or crashed) without starting a response.
            item = await chunks.get()
            if isinstance(item, BaseException):
                raise item
            raise RuntimeError(
                "ASGI application returned without starting a response."
            )

        stream = _StreamingByteStream(
            chunks, task, disconnect, self.raise_app_exceptions
        )
        return httpx.Response(status_code, headers=response_headers, stream=stream)


class AsyncTestClient(httpx.AsyncClient):
    """An ``httpx.AsyncClient`` wired directly to a Responder application.

    The async mirror of ``api.requests``: requests are dispatched in-process
    over an ASGI transport — no server, no sockets, no ports. Use it from
    async tests (e.g. with ``asyncio.run`` or ``pytest-asyncio``) to exercise
    genuinely-async behavior such as SSE streams, concurrent handlers, and
    app-scoped dependency teardown::

        from responder.testing import AsyncTestClient

        async def main():
            async with AsyncTestClient(api) as client:
                r = await client.get("/")
                assert r.status_code == 200

        asyncio.run(main())

    Entering the client as an async context manager also runs the
    application's lifespan, so startup/shutdown events fire (the counterpart
    of ``with api.requests as session:``). Requests made without ``async
    with`` work too, but skip lifespan events — just like ``api.requests``.

    Unlike ``httpx.ASGITransport``, the underlying
    :class:`ASGIStreamingTransport` streams response bodies live, so
    ``client.stream(...)`` can read from endless SSE streams (heartbeats
    included) and simply close them when done.

    :param app: The Responder ``API`` instance (or any ASGI app) under test.
    :param base_url: Base URL for requests; defaults to ``"http://;"``,
        matching ``api.requests``.
    :param raise_app_exceptions: If ``True`` (default), unhandled application
        exceptions propagate into the test instead of rendering a 500.
    :param kwargs: Additional keyword arguments for ``httpx.AsyncClient``.
    """

    def __init__(
        self,
        app: Any,
        base_url: str = "http://;",
        *,
        raise_app_exceptions: bool = True,
        **kwargs: Any,
    ) -> None:
        transport = ASGIStreamingTransport(
            app, raise_app_exceptions=raise_app_exceptions
        )
        super().__init__(transport=transport, base_url=base_url, **kwargs)
        self._responder_app = app
        self._responder_lifespan: _Lifespan | None = None

    async def __aenter__(self) -> AsyncTestClient:
        await super().__aenter__()
        lifespan = _Lifespan(self._responder_app)
        try:
            await lifespan.startup()
        except BaseException:
            await super().__aexit__(None, None, None)
            raise
        self._responder_lifespan = lifespan
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        lifespan, self._responder_lifespan = self._responder_lifespan, None
        try:
            if lifespan is not None:
                await lifespan.shutdown()
        finally:
            await super().__aexit__(exc_type, exc_value, traceback)


@dataclass
class SSEEvent:
    """One parsed Server-Sent Events message.

    :var data: The event payload (multi-line ``data:`` fields joined with
        ``\\n``).
    :var event: The event type; ``"message"`` when the frame carried no
        ``event:`` field (the SSE default).
    :var id: The event id, or ``None`` when the frame carried none.
    :var retry: The reconnection delay in milliseconds, or ``None``.
    """

    data: str = ""
    event: str = "message"
    id: str | None = None
    retry: int | None = None

    def json(self) -> Any:
        """The ``data`` payload decoded as JSON.

        The server-side ``resp.sse`` JSON-encodes ``dict``/``list`` data, so
        this is the natural way to read it back in tests.
        """
        return _json.loads(self.data)


class _SSEParser:
    """Incremental ``text/event-stream`` parser, fed line by line.

    The mirror of the server-side SSE framing: ``feed`` consumes one line
    (terminator already stripped) and returns an :class:`SSEEvent` when a
    blank line completes a frame. Comment lines (``: heartbeat``) are ignored,
    like a browser ``EventSource`` would.
    """

    def __init__(self) -> None:
        self._data: list[str] = []
        self._event: str | None = None
        self._id: str | None = None
        self._retry: int | None = None
        self._seen_field = False

    def feed(self, line: str) -> SSEEvent | None:
        if line == "":
            return self.flush()
        if line.startswith(":"):  # comment / keepalive
            return None
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "data":
            self._data.append(value)
        elif name == "event":
            self._event = value
        elif name == "id":
            self._id = value
        elif name == "retry":
            try:
                self._retry = int(value)
            except ValueError:
                return None
        else:  # unknown field: ignored, per the SSE specification
            return None
        self._seen_field = True
        return None

    def flush(self) -> SSEEvent | None:
        """Dispatch any pending fields as an event (``None`` if there are none).

        Unlike a strict browser parser, a frame that set only ``event:``/
        ``id:``/``retry:`` (no ``data:``) is still reported — tests usually
        want to see exactly what the server sent.
        """
        if not self._seen_field:
            return None
        event = SSEEvent(
            data="\n".join(self._data),
            event=self._event or "message",
            id=self._id,
            retry=self._retry,
        )
        self._data = []
        self._event = self._id = None
        self._retry = None
        self._seen_field = False
        return event


def parse_sse(text: str) -> list[SSEEvent]:
    """Parse a complete ``text/event-stream`` body into :class:`SSEEvent`\\ s.

    Handy with the sync test client, which buffers the whole (finite)
    stream::

        r = api.requests.get("/events")
        events = responder.testing.parse_sse(r.text)
        assert events[0].json() == {"n": 0}

    :param text: The response body text.
    """
    parser = _SSEParser()
    events: list[SSEEvent] = []
    for line in text.splitlines():
        event = parser.feed(line)
        if event is not None:
            events.append(event)
    tail = parser.flush()
    if tail is not None:
        events.append(tail)
    return events


async def iter_sse(response: httpx.Response) -> AsyncIterator[SSEEvent]:
    """Iterate parsed :class:`SSEEvent`\\ s from a streaming httpx response.

    Pair it with :class:`AsyncTestClient` and ``client.stream(...)`` to read
    events as the server produces them — including from infinite streams,
    since you can stop iterating (and close the stream) at any point::

        async with AsyncTestClient(api) as client:
            async with client.stream("GET", "/events") as r:
                async for event in iter_sse(r):
                    assert event.event == "tick"
                    break  # done after the first event

    Comment lines (heartbeats) are skipped.

    :param response: A streaming (or fully-read) ``httpx.Response`` whose body
        is ``text/event-stream``.
    """
    parser = _SSEParser()
    async for line in response.aiter_lines():
        event = parser.feed(line)
        if event is not None:
            yield event
    tail = parser.flush()
    if tail is not None:
        yield tail


async def collect_sse(response: httpx.Response) -> list[SSEEvent]:
    """Collect all :class:`SSEEvent`\\ s from a finite streaming response.

    The one-shot form of :func:`iter_sse`::

        async with AsyncTestClient(api) as client:
            r = await client.get("/events")
            events = await collect_sse(r)

    :param response: A streaming (or fully-read) ``httpx.Response`` whose body
        is ``text/event-stream``.
    """
    return [event async for event in iter_sse(response)]
