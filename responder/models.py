from __future__ import annotations

import functools
import hashlib
import inspect
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

__all__ = ["Request", "Response", "QueryDict"]

try:
    import chardet
except ImportError:
    chardet = None  # type: ignore[assignment]
from starlette.background import BackgroundTasks
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request as StarletteRequest
from starlette.requests import State
from starlette.responses import (
    Response as StarletteResponse,
)
from starlette.responses import (
    StreamingResponse as StarletteStreamingResponse,
)

from .statics import DEFAULT_ENCODING
from .status_codes import HTTP_301  # type: ignore[attr-defined]


class CaseInsensitiveDict(dict):
    """A case-insensitive dict for HTTP headers."""

    def __setitem__(self, key, value):
        super().__setitem__(key.lower(), value)

    def __getitem__(self, key):
        return super().__getitem__(key.lower())

    def __delitem__(self, key):
        super().__delitem__(key.lower())

    def __contains__(self, key):
        return super().__contains__(key.lower())

    def get(self, key, default=None):
        return super().get(key.lower(), default)

    def pop(self, key, *args):
        return super().pop(key.lower(), *args)

    def setdefault(self, key, default=None):
        return super().setdefault(key.lower(), default)

    def update(self, other=None, /, **kwargs):
        if other:
            for key, value in other.items():
                self[key] = value
        for key, value in kwargs.items():
            self[key] = value


class QueryDict(dict):
    """A dictionary for query string parameters that handles multi-value keys.

    Single-value access returns the last value for a key. Use :meth:`get_list`
    to retrieve all values for a multi-value parameter.
    """

    def __init__(self, query_string):
        self.update(parse_qs(query_string))

    def __getitem__(self, key):
        """
        Return the last data value for this key, or [] if it's an empty list;
        raise KeyError if not found.
        """
        list_ = super().__getitem__(key)
        try:
            return list_[-1]
        except IndexError:
            return []

    def get(self, key, default=None):
        """
        Return the last data value for the passed key. If key doesn't exist
        or value is an empty list, return `default`.
        """
        try:
            val = self[key]
        except KeyError:
            return default
        if val == []:
            return default
        return val

    def _get_list(self, key, default=None, force_list=False):
        """
        Return a list of values for the key.

        Used internally to manipulate values list. If force_list is True,
        return a new copy of values.
        """
        try:
            values = super().__getitem__(key)
        except KeyError:
            if default is None:
                return []
            return default
        else:
            if force_list:
                values = list(values) if values is not None else None
            return values

    def get_list(self, key, default=None):
        """
        Return the list of values for the key. If key doesn't exist, return a
        default value.
        """
        return self._get_list(key, default, force_list=True)

    def items(self):
        """
        Yield (key, value) pairs, where value is the last item in the list
        associated with the key.
        """
        for key in self:
            yield key, self[key]

    def items_list(self):
        """
        Yield (key, value) pairs, where value is the the list.
        """
        yield from super().items()


class Request:
    """An HTTP request, passed to each view as the first argument.

    Provides access to headers, cookies, query parameters, the request body,
    session data, and more. Most properties are synchronous; reading the body
    (via :attr:`content`, :attr:`text`, or :meth:`media`) requires ``await``.
    """

    __slots__ = [
        "_starlette",
        "formats",
        "_headers",
        "_encoding",
        "api",
        "_content",
        "_cookies",
        "_url",
        "_params",
        "_max_size",
    ]

    def __init__(self, scope, receive, api=None, formats=None):
        self._starlette = StarletteRequest(scope, receive)
        self.formats = formats
        self._encoding = None
        self.api = api
        self._content = None
        self._url = None
        self._params = None
        self._headers = None
        self._cookies = None
        self._max_size = scope.get("max_request_size")

    @property
    def session(self):
        """The session data, in dict form, from the Request."""
        return self._starlette.session

    @property
    def headers(self):
        """A case-insensitive dictionary, containing all headers sent in the Request."""
        if self._headers is None:
            headers: CaseInsensitiveDict = CaseInsensitiveDict()
            for key, value in self._starlette.headers.items():
                headers[key] = value
            self._headers = headers
        return self._headers

    @property
    def mimetype(self):
        """The MIME type of the request body, from the ``Content-Type`` header."""
        return self.headers.get("Content-Type", "")

    @property
    def is_json(self):
        """Returns ``True`` if the request content type is JSON."""
        return "json" in self.mimetype

    @property
    def method(self):
        """The incoming HTTP method used for the request, lower-cased."""
        return self._starlette.method.lower()

    @property
    def full_url(self):
        """The full URL of the Request, query parameters and all."""
        return str(self._starlette.url)

    @property
    def url(self):
        """The parsed URL of the Request."""
        if self._url is None:
            self._url = urlparse(self.full_url)
        return self._url

    @property
    def cookies(self):
        """The cookies sent in the Request, as a dictionary."""
        if self._cookies is None:
            cookies = {}
            cookie_header = self.headers.get("Cookie", "")

            bc: SimpleCookie = SimpleCookie(cookie_header)
            for key, morsel in bc.items():
                cookies[key] = morsel.value

            self._cookies = cookies

        return self._cookies

    @property
    def params(self):
        """A dictionary of the parsed query parameters used for the Request."""
        if self._params is None:
            self._params = QueryDict(self.url.query)
        return self._params

    @property
    def path_params(self) -> dict:
        """The path parameters extracted from the URL route."""
        return self._starlette.path_params

    @property
    def client(self):
        """The client's address as a (host, port) named tuple, or None."""
        return self._starlette.client

    @property
    def state(self) -> State:
        """
        Use the state to store additional information.

        This can be a very helpful feature, if you want to hand over
        information from a middelware or a route decorator to the
        actual route handler.

        Usage: ``request.state.time_started = time.time()``
        """
        return self._starlette.state

    @property
    async def encoding(self):
        """The encoding of the Request's body. Can be set, manually. Must be awaited."""
        # Use the user-set encoding first.
        if self._encoding:
            return self._encoding

        return await self.apparent_encoding

    @encoding.setter
    def encoding(self, value):
        self._encoding = value

    def _check_size(self, size):
        if self._max_size is not None and size > self._max_size:
            raise HTTPException(status_code=413, detail="Request body too large")

    @property
    async def content(self):
        """The Request body, as bytes. Must be awaited."""
        if self._content is None:
            declared = self.headers.get("Content-Length")
            if declared and declared.isdigit():
                self._check_size(int(declared))
            # Enforce the size cap while reading, so an oversized chunked
            # (or lying-Content-Length) body is rejected before it is fully
            # resident in memory — not buffered first and checked after.
            chunks: list[bytes] = []
            received = 0
            async for chunk in self._starlette.stream():
                received += len(chunk)
                self._check_size(received)
                chunks.append(chunk)
            self._content = b"".join(chunks)
        return self._content

    async def stream(self):
        """Iterate over the raw request body in chunks, without buffering.

        Useful for large uploads. Once streamed, the body cannot be read
        again via :attr:`content`, :attr:`text`, or :meth:`media`.

        Usage::

            @api.route("/upload", methods=["POST"])
            async def upload(req, resp):
                async with await anyio.open_file(path, "wb") as f:
                    async for chunk in req.stream():
                        await f.write(chunk)

        """
        if self._content is not None:
            yield self._content
            return
        received = 0
        async for chunk in self._starlette.stream():
            if chunk:
                received += len(chunk)
                self._check_size(received)
                yield chunk

    @property
    async def text(self):
        """The Request body, as unicode. Must be awaited."""
        return (await self.content).decode(await self.encoding)

    @property
    async def declared_encoding(self):
        if "Encoding" in self.headers:
            return self.headers["Encoding"]
        return None

    @property
    async def apparent_encoding(self):
        """The apparent encoding, detected automatically. Must be awaited.

        Uses chardet for detection if installed, otherwise falls back to UTF-8.
        """
        declared_encoding = await self.declared_encoding

        if declared_encoding:
            return declared_encoding

        if chardet is not None:
            return chardet.detect(await self.content)["encoding"] or DEFAULT_ENCODING

        return DEFAULT_ENCODING

    @property
    def is_secure(self):
        """``True`` if the request was made over HTTPS."""
        return self.url.scheme == "https"

    def accepts(self, content_type):
        """Returns ``True`` if the incoming Request accepts the given ``content_type``."""
        return content_type in self.headers.get("Accept", [])

    async def media(self, format: str | Callable | None = None):  # noqa: A002
        """Renders incoming json/yaml/form data as Python objects. Must be awaited.

        :param format: The name of the format being used.
                       Alternatively, accepts a custom callable for the format type.
        """

        if format is None:
            format = "yaml" if "yaml" in self.mimetype else "json"  # noqa: A001
            format = "form" if "form" in self.mimetype else format  # noqa: A001

        formatter: Callable
        if isinstance(format, str):
            try:
                formatter = self.formats[format]
            except KeyError as ex:
                raise ValueError(f"Unable to process data in '{format}' format") from ex

        elif callable(format):
            formatter = format

        else:
            raise TypeError(f"Invalid 'format' argument: {format}")

        return await formatter(self)


class RangeNotSatisfiable(Exception):
    """The request's ``Range`` header cannot be satisfied (→ 416)."""


def _parse_byte_range(header, size):
    """Parse a single-range ``Range`` header against a resource of ``size``.

    Returns ``(start, end)`` (inclusive), ``None`` when the header is absent,
    malformed, or multi-range (serve the full resource per RFC 7233), or
    raises :class:`RangeNotSatisfiable` (→ 416).
    """
    if not header or not header.startswith("bytes=") or size == 0:
        return None
    spec = header[len("bytes=") :].strip()
    if "," in spec:  # Multiple ranges unsupported; serve the full resource.
        return None

    start_s, sep, end_s = spec.partition("-")
    if not sep:
        return None
    try:
        if not start_s:  # Suffix range: bytes=-N (the last N bytes).
            suffix = int(end_s)
            if suffix <= 0:
                raise RangeNotSatisfiable()
            return max(0, size - suffix), size - 1
        start = int(start_s)
        end = min(int(end_s), size - 1) if end_s else size - 1
    except ValueError:
        return None  # Malformed numbers: ignore the header.

    if start >= size or start > end:
        raise RangeNotSatisfiable()
    return start, end


def _resolve_within(path, root):
    """Resolve ``path`` under ``root``, refusing any escape (→ 404).

    ``path`` is treated as relative to ``root`` (a leading ``/`` is ignored),
    and symlinks are followed before the containment check, so neither ``..``
    nor a symlink can reach outside ``root``.
    """
    from pathlib import Path

    base = Path(root).resolve()
    target = (base / str(path).lstrip("/")).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found") from None
    return target


def _is_external_url(location):
    """Whether ``location`` points off-site (absolute, or protocol-relative)."""
    if location.startswith("//"):
        return True
    parsed = urlparse(location)
    return bool(parsed.scheme or parsed.netloc)


def content_setter(mimetype):
    def getter(instance):
        return instance.content

    def setter(instance, value):
        instance.content = value
        instance.mimetype = mimetype

    return property(fget=getter, fset=setter)


class Response:
    """An HTTP response, passed to each view as the second argument.

    Mutate this object to control what gets sent back to the client. Set
    :attr:`text`, :attr:`html`, :attr:`media`, or :attr:`content` to define
    the body. Use :attr:`headers` and :meth:`set_cookie` to control metadata.

    :var text: Set the response body as plain text (sets ``Content-Type: text/plain``).
    :var html: Set the response body as HTML (sets ``Content-Type: text/html``).
    :var media: Set a Python object (dict, list) to be serialized as JSON (or negotiated format).
    :var content: Set the raw response body as bytes.
    :var status_code: The HTTP status code (e.g. ``200``, ``404``). Defaults to ``200`` if not set.
    :var headers: A dict of response headers.
    :var cookies: A ``SimpleCookie`` holding cookies to set on the response.
    :var session: A dict of session data. Changes are persisted in a signed cookie.
    :var etag: Entity tag for the response. When the request's ``If-None-Match`` matches, an automatic ``304 Not Modified`` is sent instead of the body.
    :var last_modified: A ``datetime`` (or HTTP-date string) for ``Last-Modified``. Honors ``If-Modified-Since`` with automatic ``304`` responses.
    """  # noqa: E501

    __slots__ = [
        "req",
        "status_code",
        "content",
        "encoding",
        "media",
        "headers",
        "formats",
        "cookies",
        "session",
        "mimetype",
        "etag",
        "last_modified",
        "_stream",
        "_auto_etag",
        "_background",
        "_deferred_content",
    ]

    text = content_setter("text/plain")
    html = content_setter("text/html")

    def __init__(self, req, *, formats, auto_etag=False):
        self.req = req
        self.status_code: int | None = None
        self.content = None
        self.mimetype = None
        self.encoding = DEFAULT_ENCODING
        self.media = None
        self._stream = None
        self.etag = None
        self.last_modified = None
        self._auto_etag = auto_etag
        self._background = None
        self._deferred_content = None
        self.headers = {}
        self.formats = formats
        self.cookies: SimpleCookie = SimpleCookie()
        self.session = req.session

    def stream(self, func, *args, **kwargs):
        """Set up a streaming response from an async generator function.

        The generator yields chunks of bytes that are sent to the client
        as they are produced, without buffering the full response in memory.

        Usage::

            @api.route("/stream")
            async def stream_data(req, resp):
                @resp.stream
                async def body():
                    for i in range(10):
                        yield f"chunk {i}\\n".encode()

        :param func: An async generator function that yields response chunks.
        """
        assert inspect.isasyncgenfunction(func)

        self._stream = functools.partial(func, *args, **kwargs)

        return func

    def sse(self, func, *args, **kwargs):
        """Set up Server-Sent Events streaming.

        Usage::

            @api.route("/events")
            async def events(req, resp):
                @resp.sse
                async def stream():
                    for i in range(10):
                        yield {"data": f"message {i}"}

        Each yielded dict can have: data, event, id, retry.
        Yielding a string is treated as data.
        """
        assert inspect.isasyncgenfunction(func)

        async def sse_generator():
            async for event in func(*args, **kwargs):
                if isinstance(event, str):
                    yield f"data: {event}\n\n".encode()
                elif isinstance(event, dict):
                    parts = []
                    if "event" in event:
                        parts.append(f"event: {event['event']}")
                    if "id" in event:
                        parts.append(f"id: {event['id']}")
                    if "retry" in event:
                        parts.append(f"retry: {event['retry']}")
                    data = event.get("data", "")
                    for line in str(data).split("\n"):
                        parts.append(f"data: {line}")
                    parts.append("")
                    parts.append("")
                    yield "\n".join(parts).encode()
                else:
                    yield f"data: {event}\n\n".encode()

        self._stream = sse_generator
        self.mimetype = "text/event-stream"
        self.headers["Cache-Control"] = "no-cache"
        self.headers["Connection"] = "keep-alive"

        return func

    def _set_file_mimetype(self, path, content_type):
        if content_type:
            self.mimetype = content_type
        else:
            import mimetypes

            guessed = mimetypes.guess_type(str(path))[0]
            self.mimetype = guessed or "application/octet-stream"

    def _requested_range(self, size):
        """The (start, end) byte range to serve, or None for the full file.

        Sets ``Accept-Ranges``, and on a satisfiable range, the ``206``
        status and ``Content-Range`` header. Unsatisfiable ranges raise
        :class:`RangeNotSatisfiable` after marking the response ``416``.
        """
        self.headers["Accept-Ranges"] = "bytes"
        if self.req.method not in ("get", "head"):
            return None

        try:
            byte_range = _parse_byte_range(self.req.headers.get("Range"), size)
        except RangeNotSatisfiable:
            self.status_code = 416
            self.headers["Content-Range"] = f"bytes */{size}"
            self.content = b""
            raise

        if byte_range is None:
            return None

        start, end = byte_range
        self.status_code = 206
        self.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return byte_range

    def stream_file(self, path, *, content_type=None, chunk_size=8192, root=None):
        """Stream a file without loading it entirely into memory.

        Supports HTTP range requests (``Range: bytes=...``) with ``206``
        partial responses, enabling video seeking and resumable downloads.

        :param path: Path to the file.
        :param content_type: Optional MIME type override.
        :param chunk_size: Size of chunks to read (default 8192 bytes).
        :param root: If given, ``path`` is resolved under this directory and any
                     attempt to escape it (via ``..`` or a symlink) yields a
                     ``404`` — use this whenever ``path`` is built from user input.
        """
        from pathlib import Path as PathType

        path = PathType(path) if root is None else _resolve_within(path, root)
        self._set_file_mimetype(path, content_type)

        size = path.stat().st_size
        try:
            byte_range = self._requested_range(size)
        except RangeNotSatisfiable:
            return
        start, end = byte_range if byte_range else (0, size - 1)

        async def file_generator():
            import anyio

            remaining = end - start + 1 if size else 0
            async with await anyio.open_file(path, "rb") as f:
                if start:
                    await f.seek(start)
                while remaining > 0:
                    chunk = await f.read(min(chunk_size, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        self._stream = file_generator

    def file(self, path, *, content_type=None, root=None):
        """Serve a file from disk as the response.

        Supports HTTP range requests (``Range: bytes=...``) with ``206``
        partial responses. The file's bytes are read in a worker thread when
        the response is sent, so calling this from an ``async`` handler never
        blocks the event loop.

        :param path: Path to the file to serve.
        :param content_type: Optional MIME type override.
        :param root: If given, ``path`` is resolved under this directory and any
                     attempt to escape it (via ``..`` or a symlink) yields a
                     ``404`` — use this whenever ``path`` is built from user input.
        """
        from pathlib import Path

        path = Path(path) if root is None else _resolve_within(path, root)
        self._set_file_mimetype(path, content_type)

        size = path.stat().st_size
        try:
            byte_range = self._requested_range(size)
        except RangeNotSatisfiable:
            return

        start, end = byte_range if byte_range else (0, size - 1)

        def _read() -> bytes:
            if not size:
                return b""
            with path.open("rb") as f:
                if start:
                    f.seek(start)
                return f.read(end - start + 1)

        async def _deferred() -> bytes:
            return await run_in_threadpool(_read)

        self._deferred_content = _deferred

    def download(self, path, *, filename=None, content_type=None, root=None):
        """Serve a file as an attachment, prompting the browser to download.

        Streams the file (with range support, so downloads are resumable)
        and sets ``Content-Disposition``.

        :param path: Path to the file to serve.
        :param filename: Download name presented to the client.
                         Defaults to the file's own name.
        :param content_type: Optional MIME type override.
        :param root: If given, ``path`` is resolved under this directory and any
                     escape attempt yields a ``404`` (see :meth:`file`).
        """
        from pathlib import Path
        from urllib.parse import quote

        path = Path(path) if root is None else _resolve_within(path, root)
        name = filename or path.name

        self.stream_file(path, content_type=content_type)
        try:
            name.encode("ascii")
            disposition = f'attachment; filename="{name}"'
        except UnicodeEncodeError:
            disposition = f"attachment; filename*=UTF-8''{quote(name)}"
        self.headers["Content-Disposition"] = disposition

    def render(self, template, *args, **kwargs):
        r"""Render a Jinja2 template as the HTML response body.

        Shorthand for ``resp.html = api.template(...)``, using the owning
        API's ``templates_dir``.

        :param template: The template filename.
        :param \*args: Data to pass into the template.
        :param \*\*kwargs: Data to pass into the template.

        Usage::

            @api.route("/")
            def home(req, resp):
                resp.render("home.html", user="kenneth")

        """
        if self.req.api is None:
            raise RuntimeError(
                "resp.render() requires the Response to be bound to an API"
            )
        self.html = self.req.api.template(template, *args, **kwargs)

    def background(self, func, *args, **kwargs):
        """Schedule a task to run after the response has been sent.

        Unlike ``api.background`` (which runs immediately in a thread pool),
        tasks scheduled here are deferred until the client has the response,
        so they never delay it. Sync and async functions both work. Multiple
        tasks run in the order scheduled.

        Usage::

            @api.route("/signup", methods=["POST"])
            async def signup(req, resp):
                resp.media = {"ok": True}
                resp.background(send_welcome_email, "user@example.com")

        """
        if self._background is None:
            self._background = BackgroundTasks()
        self._background.add_task(func, *args, **kwargs)
        return func

    def cache_control(self, **directives):
        """Set the ``Cache-Control`` header from keyword directives.

        Underscores become hyphens; ``True`` renders a bare directive,
        other values render ``name=value``::

            resp.cache_control(public=True, max_age=3600)
            # Cache-Control: public, max-age=3600

            resp.cache_control(no_store=True)
            # Cache-Control: no-store

        """
        parts = []
        for key, value in directives.items():
            if value is False or value is None:
                continue
            name = key.replace("_", "-")
            parts.append(name if value is True else f"{name}={value}")
        self.headers["Cache-Control"] = ", ".join(parts)

    def redirect(
        self, location, *, set_text=True, status_code=HTTP_301, allow_external=True
    ):
        """Redirect the client to a different URL.

        :param location: The URL to redirect to.
        :param set_text: If ``True``, set a default redirect message as the body.
        :param status_code: The HTTP status code (default ``301``).
        :param allow_external: If ``False``, refuse (with a ``400``) to redirect
                               to an absolute or protocol-relative URL — pass
                               this whenever ``location`` comes from user input,
                               to prevent open redirects.
        """
        if not allow_external and _is_external_url(location):
            raise HTTPException(
                status_code=400, detail="Refusing to redirect to an external URL"
            )
        self.status_code = status_code
        if set_text:
            self.text = f"Redirecting to: {location}"
        self.headers.update({"Location": location})

    @property
    async def body(self):
        # A file scheduled via resp.file() is read here (off the event loop).
        if self._deferred_content is not None and self.content is None:
            self.content = await self._deferred_content()

        if self._stream is not None:
            headers = {}
            if self.mimetype is not None:
                headers["Content-Type"] = self.mimetype
            return (self._stream(), headers)

        if self.content is not None:
            headers = {}
            content = self.content
            if self.mimetype is not None:
                headers["Content-Type"] = self.mimetype
            if self.mimetype == "text/plain" and self.encoding is not None:
                headers["Encoding"] = self.encoding
                if isinstance(content, str):
                    content = content.encode(self.encoding)
            return (content, headers)

        for format_ in self.formats:
            if self.req.accepts(format_):
                encoded = await self.formats[format_](self, encode=True)
                # Formats that can't encode (e.g. form, files) return None.
                if encoded is not None:
                    return encoded, {}

        # Default to JSON anyway.
        return (
            await self.formats["json"](self, encode=True),
            {"Content-Type": "application/json"},
        )

    def set_cookie(
        self,
        key,
        value="",
        expires=None,
        path="/",
        domain=None,
        max_age=None,
        secure=False,
        httponly=True,
        samesite="lax",
    ):
        """Set a cookie on the response with full control over directives.

        :param key: The cookie name.
        :param value: The cookie value.
        :param expires: Expiration date string (e.g. ``"Thu, 01 Jan 2026 00:00:00 GMT"``).
        :param path: URL path the cookie applies to (default ``"/"``).
        :param domain: Domain the cookie is valid for.
        :param max_age: Maximum age in seconds before the cookie expires.
        :param secure: If ``True``, cookie is only sent over HTTPS.
        :param httponly: If ``True`` (default), cookie is inaccessible to JavaScript.
        :param samesite: Cross-site behavior: ``"lax"`` (default), ``"strict"``,
                         ``"none"``, or ``None`` to omit the directive.

        Usage::

            resp.set_cookie(
                "token", value="abc123",
                max_age=3600, secure=True, httponly=True,
            )

        """
        self.cookies[key] = value
        morsel = self.cookies[key]
        if expires is not None:
            morsel["expires"] = expires
        if path is not None:
            morsel["path"] = path
        if domain is not None:
            morsel["domain"] = domain
        if max_age is not None:
            morsel["max-age"] = max_age
        morsel["secure"] = secure
        morsel["httponly"] = httponly
        if samesite is not None:
            morsel["samesite"] = samesite

    def _prepare_cookies(self, starlette_response):
        cookie_header = (
            (b"set-cookie", morsel.output(header="").lstrip().encode("latin-1"))
            for morsel in self.cookies.values()
        )
        starlette_response.raw_headers.extend(cookie_header)

    @property
    def _normalized_etag(self):
        etag = str(self.etag)
        if etag.startswith(('"', "W/")):
            return etag
        return f'"{etag}"'

    @property
    def _last_modified_header(self):
        if isinstance(self.last_modified, datetime):
            value = self.last_modified
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return format_datetime(value, usegmt=True)
        return str(self.last_modified)

    def _is_not_modified(self):
        """Whether the request's conditional headers match this response."""
        if self.req.method not in ("get", "head"):
            return False
        if self.status_code not in (None, 200):
            return False

        # If-None-Match takes precedence over If-Modified-Since (RFC 7232).
        if_none_match = self.req.headers.get("If-None-Match")
        if if_none_match and self.etag is not None:
            if if_none_match.strip() == "*":
                return True

            def core(tag):
                return tag[2:] if tag.startswith("W/") else tag

            tags = [core(t.strip()) for t in if_none_match.split(",")]
            return core(self._normalized_etag) in tags

        if_modified_since = self.req.headers.get("If-Modified-Since")
        if if_modified_since and self.last_modified is not None:
            try:
                since = parsedate_to_datetime(if_modified_since)
                current = parsedate_to_datetime(self._last_modified_header)
            except (TypeError, ValueError):
                return False
            return current <= since

        return False

    async def __call__(self, scope, receive, send):
        body = None
        headers: dict = {}
        built = False

        if (
            self._auto_etag
            and self.etag is None
            and self._stream is None
            and self.req.method in ("get", "head")
            and self.status_code in (None, 200)
        ):
            body, headers = await self.body
            built = True
            raw = (
                body
                if isinstance(body, bytes)
                else str(body).encode(self.encoding or DEFAULT_ENCODING)
            )
            self.etag = hashlib.md5(raw, usedforsecurity=False).hexdigest()

        if self.etag is not None or self.last_modified is not None:
            if self.etag is not None:
                self.headers["ETag"] = self._normalized_etag
            if self.last_modified is not None:
                self.headers["Last-Modified"] = self._last_modified_header

            if self._is_not_modified():
                not_modified = StarletteResponse(
                    status_code=304, headers=self.headers, background=self._background
                )
                self._prepare_cookies(not_modified)
                await not_modified(scope, receive, send)
                return

        if not built:
            body, headers = await self.body
        if self.headers:
            headers.update(self.headers)

        response_cls: type[StarletteResponse] | type[StarletteStreamingResponse]
        if self._stream is not None:
            response_cls = StarletteStreamingResponse
        else:
            response_cls = StarletteResponse

        response = response_cls(
            body,
            status_code=self.status_code_safe,
            headers=headers,
            background=self._background,
        )
        self._prepare_cookies(response)

        await response(scope, receive, send)

    @property
    def ok(self):
        """``True`` if the status code is in the 2xx range (success)."""
        return 200 <= self.status_code_safe < 300

    @property
    def status_code_safe(self) -> int:
        """Return the status code, raising ``RuntimeError`` if it hasn't been set."""
        if self.status_code is None:
            raise RuntimeError("HTTP status code has not been defined")
        return self.status_code
