from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

__all__ = ["Request", "Response", "QueryDict"]

try:
    import chardet
except ImportError:
    chardet = None  # type: ignore[assignment]
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

    def __contains__(self, key):
        return super().__contains__(key.lower())

    def get(self, key, default=None):
        return super().get(key.lower(), default)

    def update(self, other=None, **kwargs):
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
    ]

    def __init__(self, scope, receive, api=None, formats=None):
        self._starlette = StarletteRequest(scope, receive)
        self.formats = formats
        self._encoding = None
        self.api = api
        self._content = None

        headers: CaseInsensitiveDict = CaseInsensitiveDict()
        for key, value in self._starlette.headers.items():
            headers[key] = value

        self._headers = headers
        self._cookies = None

    @property
    def session(self):
        """The session data, in dict form, from the Request."""
        return self._starlette.session

    @property
    def headers(self):
        """A case-insensitive dictionary, containing all headers sent in the Request."""
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
        return urlparse(self.full_url)

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
        try:
            return QueryDict(self.url.query)
        except AttributeError:
            return QueryDict({})

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

    @property
    async def content(self):
        """The Request body, as bytes. Must be awaited."""
        if not self._content:
            self._content = await self._starlette.body()
        return self._content

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

    async def media(self, format: str | Callable = None):  # noqa: A002
        """Renders incoming json/yaml/form data as Python objects. Must be awaited.

        :param format: The name of the format being used.
                       Alternatively, accepts a custom callable for the format type.
        """

        if format is None:
            format = "yaml" if "yaml" in self.mimetype or "" else "json"  # noqa: A001
            format = "form" if "form" in self.mimetype or "" else format  # noqa: A001

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
        "_stream",
    ]

    text = content_setter("text/plain")
    html = content_setter("text/html")

    def __init__(self, req, *, formats):
        self.req = req
        self.status_code: int | None = None
        self.content = None
        self.mimetype = None
        self.encoding = DEFAULT_ENCODING
        self.media = None
        self._stream = None
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

    def stream_file(self, path, *, content_type=None, chunk_size=8192):
        """Stream a file without loading it entirely into memory.

        :param path: Path to the file.
        :param content_type: Optional MIME type override.
        :param chunk_size: Size of chunks to read (default 8192 bytes).
        """
        from pathlib import Path as PathType

        path = PathType(path)

        if content_type:
            self.mimetype = content_type
        else:
            import mimetypes

            guessed = mimetypes.guess_type(str(path))[0]
            self.mimetype = guessed or "application/octet-stream"

        async def file_generator():
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

        self._stream = file_generator

    def file(self, path, *, content_type=None):
        """Serve a file from disk as the response.

        :param path: Path to the file to serve.
        :param content_type: Optional MIME type override.
        """
        from pathlib import Path

        path = Path(path)
        self.content = path.read_bytes()

        if content_type:
            self.mimetype = content_type
        else:
            import mimetypes

            guessed = mimetypes.guess_type(str(path))[0]
            self.mimetype = guessed or "application/octet-stream"

    def redirect(self, location, *, set_text=True, status_code=HTTP_301):
        """Redirect the client to a different URL.

        :param location: The URL to redirect to.
        :param set_text: If ``True``, set a default redirect message as the body.
        :param status_code: The HTTP status code (default ``301``).
        """
        self.status_code = status_code
        if set_text:
            self.text = f"Redirecting to: {location}"
        self.headers.update({"Location": location})

    @property
    async def body(self):
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
                return (await self.formats[format_](self, encode=True)), {}

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

    def _prepare_cookies(self, starlette_response):
        cookie_header = (
            (b"set-cookie", morsel.output(header="").lstrip().encode("latin-1"))
            for morsel in self.cookies.values()
        )
        starlette_response.raw_headers.extend(cookie_header)

    async def __call__(self, scope, receive, send):
        body, headers = await self.body
        if self.headers:
            headers.update(self.headers)

        response_cls: type[StarletteResponse] | type[StarletteStreamingResponse]
        if self._stream is not None:
            response_cls = StarletteStreamingResponse
        else:
            response_cls = StarletteResponse

        response = response_cls(body, status_code=self.status_code_safe, headers=headers)
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
