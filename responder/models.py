from __future__ import annotations

import asyncio
import functools
import hashlib
import inspect
import json
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlparse

__all__ = ["Request", "Response", "QueryDict", "UploadFile"]

try:
    import chardet
except ImportError:
    chardet = None  # type: ignore[assignment]
from starlette.background import BackgroundTasks
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import Request as StarletteRequest
from starlette.requests import State
from starlette.responses import (
    Response as StarletteResponse,
)
from starlette.responses import (
    StreamingResponse as StarletteStreamingResponse,
)

from .errors import PROBLEM_JSON, problem_payload_for
from .statics import DEFAULT_ENCODING
from .status_codes import HTTP_307, HTTP_308


async def _upload_file_save(
    self, path, *, chunk_size=1024 * 1024, seek_start=True, create_parents=False
):
    """Persist an uploaded file to disk without buffering it all in memory."""
    from pathlib import Path

    import anyio

    target = Path(path)
    if create_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    if seek_start:
        await self.seek(0)
    async with await anyio.open_file(target, "wb") as f:
        while True:
            chunk = await self.read(chunk_size)
            if not chunk:
                break
            await f.write(chunk)
    return target


if not hasattr(UploadFile, "save"):
    UploadFile.save = _upload_file_save  # type: ignore[attr-defined]


class CaseInsensitiveDict(dict):
    """A case-insensitive, case-preserving dict for HTTP headers.

    Lookups, membership tests, deletion, and updates match keys
    case-insensitively, while iteration preserves the casing each key was
    last set with — so ``d["content-type"] = ...`` replaces an existing
    ``Content-Type`` entry rather than adding a second one.

    HTTP allows the same header name to appear on multiple lines; when the
    mapping is built from such raw pairs (as ``req.headers`` is), single-value
    access returns the last value while :meth:`get_list` returns every value,
    in the order received.
    """

    def __init__(self, data: Any = None, /, **kwargs: Any) -> None:
        super().__init__()
        self._lower: dict[str, str] = {}
        self._multi: dict[str, list[Any]] = {}
        self.update(data, **kwargs)

    def __setitem__(self, key: str, value: Any) -> None:
        lower = key.lower()
        stored = self._lower.get(lower)
        if stored is not None and stored != key:
            super().__delitem__(stored)
        self._lower[lower] = key
        self._multi[lower] = [value]
        super().__setitem__(key, value)

    def __getitem__(self, key: str) -> Any:
        return super().__getitem__(self._lower[key.lower()])

    def __delitem__(self, key: str) -> None:
        super().__delitem__(self._lower.pop(key.lower()))
        self._multi.pop(key.lower(), None)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key.lower() in self._lower

    def __reduce__(self) -> tuple[Any, ...]:
        # Rebuild via __init__ from a plain dict of the current items (with
        # their original casing) so that pickle, copy.copy, and copy.deepcopy
        # all reconstruct the _lower index instead of replaying __setitem__
        # on an instance whose __init__ never ran (pickle) or sharing _lower
        # with the original (copy.copy). The third element restores _multi so
        # duplicate header lines (get_list) survive the round-trip; __init__
        # from a plain dict would otherwise collapse them to a single value.
        state = {lower: list(values) for lower, values in self._multi.items()}
        return (type(self), (dict(self),), state)

    def __setstate__(self, state: dict[str, list[Any]]) -> None:
        # Overlay the preserved multi-value entries onto the _multi the
        # __init__ (run with the folded single-value dict) already built.
        self._multi.update({lower: list(values) for lower, values in state.items()})

    def __ior__(self, other: Any) -> CaseInsensitiveDict:
        self.update(other)
        return self

    def __or__(self, other: Any) -> CaseInsensitiveDict:
        new = self.copy()
        new.update(other)
        return new

    def __ror__(self, other: Any) -> CaseInsensitiveDict:
        new = CaseInsensitiveDict(other)
        new.update(self)
        return new

    def get(self, key: str, default: Any = None) -> Any:
        stored = self._lower.get(key.lower())
        if stored is None:
            return default
        return super().__getitem__(stored)

    def get_list(self, key: str, default: Any = None) -> Any:
        """Return every value received for ``key``, in order.

        HTTP permits a header to appear on multiple lines (proxies append
        separate ``X-Forwarded-For``/``Via`` lines; HTTP/2 clients may split
        headers similarly), and single-value access returns only the last
        one. This returns them all, in the order they arrived. A missing key
        returns ``default`` (or ``[]`` when no default is given).

        Usage::

            hops = req.headers.get_list("X-Forwarded-For")

        """
        lower = key.lower()
        values = self._multi.get(lower)
        if values is not None:
            return list(values)
        # Defensive fallback for any key present in the single-value view
        # only (e.g. mutation through a raw ``dict`` API in a subclass).
        stored = self._lower.get(lower)
        if stored is not None:
            return [super().__getitem__(stored)]
        return [] if default is None else default

    def _add(self, key: str, value: Any) -> None:
        """Append a value for ``key``, keeping earlier ones for get_list.

        The single-value view behaves as if the key were simply re-set:
        the last value (and its casing) wins.
        """
        prior = self._multi.get(key.lower())
        prior = list(prior) if prior is not None else []
        self[key] = value
        self._multi[key.lower()] = [*prior, value]

    def pop(self, key: str, *args: Any) -> Any:
        stored = self._lower.get(key.lower())
        if stored is None:
            if args:
                return args[0]
            raise KeyError(key)
        del self._lower[key.lower()]
        self._multi.pop(key.lower(), None)
        return super().pop(stored)

    def popitem(self) -> tuple[str, Any]:
        key, value = super().popitem()
        self._lower.pop(key.lower(), None)
        self._multi.pop(key.lower(), None)
        return key, value

    def setdefault(self, key: str, default: Any = None) -> Any:
        stored = self._lower.get(key.lower())
        if stored is not None:
            return super().__getitem__(stored)
        self[key] = default
        return default

    def update(self, other: Any = None, /, **kwargs: Any) -> None:
        if other is not None:
            items = other.items() if hasattr(other, "items") else other
            for key, value in items:
                self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    def clear(self) -> None:
        super().clear()
        self._lower.clear()
        self._multi.clear()

    def copy(self) -> CaseInsensitiveDict:
        new = CaseInsensitiveDict(self)
        # Preserve multi-value entries (duplicate header lines) too.
        new._multi = {key: list(values) for key, values in self._multi.items()}
        return new

    @classmethod
    def fromkeys(cls, iterable: Any, value: Any = None) -> CaseInsensitiveDict:
        d = cls()
        for key in iterable:
            d[key] = value
        return d


class QueryDict(dict):
    """A dictionary for query string parameters that handles multi-value keys.

    Single-value access returns the last value for a key. Use :meth:`get_list`
    to retrieve all values for a multi-value parameter.
    """

    def __init__(self, query_string):
        self.update(parse_qs(query_string, keep_blank_values=True))

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

    def __setitem__(self, key, value):
        """
        Store the value for this key. A non-list value is wrapped in a
        one-element list, so single-value reads return it unchanged.
        """
        if not isinstance(value, list):
            value = [value]
        super().__setitem__(key, value)

    def setdefault(self, key, default=None):
        """
        Like ``dict.setdefault``, but stores scalars via :meth:`__setitem__`
        so single-value reads return them unchanged.
        """
        if key not in self:
            self[key] = default
        return self[key]

    def update(self, other=None, /, **kwargs):
        """
        Update from a mapping/iterable and keyword args, storing scalar
        values via :meth:`__setitem__` (lists are stored as-is). Merging
        from another :class:`QueryDict` copies each key's full stored
        list, so multi-value parameters survive intact.
        """
        if other is not None:
            if isinstance(other, QueryDict):
                # other.items() collapses to the last value per key; use the
                # stored lists so ["1", "2"] merges as ["1", "2"], not ["2"].
                items = other.items_list()
            elif hasattr(other, "items"):
                items = other.items()
            else:
                items = other
            for key, value in items:
                self[key] = value
        for key, value in kwargs.items():
            self[key] = value

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


def _parse_accept(header: str) -> list[tuple[str, str, float]]:
    """Parse an ``Accept`` header into ``(type, subtype, q)`` media ranges."""
    ranges: list[tuple[str, str, float]] = []
    for part in header.split(","):
        media, _, params = part.strip().partition(";")
        media = media.strip().lower()
        if not media:
            continue
        type_, _, subtype = media.partition("/")
        quality = 1.0
        for param in params.split(";"):
            key, _, value = param.partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 1.0
        # Keep an absent subtype empty (a bare "yaml" token) rather than
        # widening it to "*", so it doesn't match unrelated media types.
        ranges.append((type_.strip() or "*", subtype.strip(), quality))
    return ranges


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
        "_accept",
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
        self._accept = None

    @property
    def session(self):
        """The session data, in dict form, from the Request.

        Requires sessions enabled (a ``secret_key`` or ``session_backend``,
        with ``sessions != False``); raises ``RuntimeError`` otherwise.
        """
        if "session" not in self._starlette.scope:
            raise RuntimeError(
                "req.session is unavailable: sessions are disabled. Enable them "
                "with API(secret_key=...) or API(session_backend=...) (sessions "
                "are on by default unless you passed sessions=False)."
            )
        return self._starlette.session

    @property
    def csrf_token(self):
        """The session's CSRF token, minted on first access.

        Embed it in responses (or read it client-side) and send it back on
        unsafe requests in an ``X-CSRF-Token`` header or ``csrf_token`` form
        field. Requires sessions; see :attr:`session`.
        """
        from .csrf import get_csrf_token

        return get_csrf_token(self.session)

    @property
    def csrf_input(self):
        """A hidden ``<input>`` carrying :attr:`csrf_token`, for HTML forms.

        Returns markup-safe HTML, so it renders unescaped in autoescaped
        Jinja templates: pass the request into the template context and drop
        ``{{ req.csrf_input }}`` inside each ``<form method="post">``.
        """
        from markupsafe import Markup

        # Markup.format escapes its arguments, so the token can't break out
        # of the attribute even if a session store handed back something odd.
        return Markup('<input type="hidden" name="csrf_token" value="{}">').format(
            self.csrf_token
        )

    @property
    def headers(self) -> CaseInsensitiveDict:
        """A case-insensitive dictionary, containing all headers sent in the Request.

        Single-value access (``req.headers["X-Forwarded-For"]``) returns the
        last value received for a repeated header; use
        ``req.headers.get_list("X-Forwarded-For")`` to read every raw line,
        in order.
        """
        if self._headers is None:
            headers: CaseInsensitiveDict = CaseInsensitiveDict()
            # Populate from the raw header pairs so duplicate lines (e.g.
            # X-Forwarded-For appended by each proxy hop) stay recoverable
            # via headers.get_list(...).
            for key, value in self._starlette.headers.items():
                headers._add(key, value)
            self._headers = headers
        return self._headers

    @property
    def mimetype(self) -> str:
        """The MIME type of the request body, from the ``Content-Type`` header."""
        return self.headers.get("Content-Type", "")

    @property
    def is_json(self) -> bool:
        """Returns ``True`` if the request content type is JSON."""
        # Media types are case-insensitive (RFC 7231 §3.1.1.1).
        return "json" in self.mimetype.lower()

    @property
    def last_event_id(self) -> str | None:
        """The SSE ``Last-Event-ID`` header sent by a reconnecting client.

        Use it to resume an event stream from where the client left off.
        """
        return self.headers.get("Last-Event-ID")

    @property
    def method(self) -> str:
        """The HTTP method, UPPER-cased (``"GET"``, ``"POST"``, …).

        Comparisons are case-sensitive; compare against uppercase literals
        (``req.method == "GET"``). Use ``.lower()`` for the lowercase string.
        """
        return self._starlette.method.upper()

    @property
    def full_url(self) -> str:
        """The full URL of the Request, query parameters and all."""
        return str(self._starlette.url)

    @property
    def url(self):
        """The parsed URL of the Request."""
        if self._url is None:
            self._url = urlparse(self.full_url)
        return self._url

    @property
    def cookies(self) -> dict:
        """The cookies sent in the Request, as a dictionary."""
        if self._cookies is None:
            # Starlette's cookie parsing is tolerant of nonconforming tokens
            # (e.g. square brackets), where http.cookies.SimpleCookie would
            # silently drop every cookie from the first bad token onward.
            self._cookies = dict(self._starlette.cookies)

        return self._cookies

    @property
    def params(self) -> QueryDict:
        """A dictionary of the parsed query parameters used for the Request."""
        if self._params is None:
            # Read the raw query string straight from the scope instead of
            # reconstructing and re-parsing the whole URL. UTF-8 matches the
            # prior behavior (Starlette's URL decodes query_string as UTF-8);
            # errors="replace" keeps malformed bytes from raising.
            qs = self._starlette.scope.get("query_string", b"")
            if isinstance(qs, (bytes, bytearray)):
                qs = qs.decode("utf-8", errors="replace")
            self._params = QueryDict(qs)
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
            if getattr(self._starlette, "_stream_consumed", False):
                raise RuntimeError(
                    "The request body has already been streamed (e.g. by a "
                    "multipart parse via req.media('form'/'files') or "
                    "req.stream()), so the raw bytes are gone. Await "
                    "req.content before parsing if you also need the raw body."
                )
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

    async def _parsed_form(self):
        """Parse the form/multipart body once, streaming multipart from the wire.

        Multipart bodies feed Starlette's incremental parser directly from
        :meth:`stream`, so file parts spool to temporary files (rolling to disk
        past ~1 MB) instead of being held in RAM, and ``max_request_size`` is
        enforced chunk-by-chunk as bytes arrive. The parsed form is cached and
        re-readable, but the raw body is consumed by the parse: a later
        ``req.content`` raises. Awaiting ``req.content`` *before* parsing keeps
        the fully-buffered, replayable behavior.

        URL-encoded forms are small: they buffer through :attr:`content` (which
        enforces ``max_request_size``) and the body stays replayable.
        """
        starlette_req = self._starlette
        if getattr(starlette_req, "_form", None) is not None:
            return starlette_req._form
        content_type = self.headers.get("Content-Type", "")
        already_buffered = self._content is not None or hasattr(
            starlette_req, "_body"
        )
        if "multipart/form-data" in content_type.lower() and not already_buffered:
            from starlette.formparsers import MultiPartException, MultiPartParser

            try:
                form = await MultiPartParser(
                    starlette_req.headers, self.stream()
                ).parse()
            except MultiPartException as exc:
                raise HTTPException(status_code=400, detail=exc.message) from exc
            starlette_req._form = form
            return form
        if not hasattr(starlette_req, "_body"):
            starlette_req._body = await self.content
        return await starlette_req.form()

    @property
    async def text(self):
        """The Request body, as unicode. Must be awaited."""
        return (await self.content).decode(await self.encoding)

    @property
    async def declared_encoding(self):
        # An explicit (non-standard) "Encoding" header wins, for back-compat.
        if "Encoding" in self.headers:
            return self.headers["Encoding"]
        # Otherwise honor the standard charset= parameter of Content-Type,
        # rather than guessing with chardet.
        content_type = self.headers.get("Content-Type", "")
        for param in content_type.split(";")[1:]:
            key, _, value = param.partition("=")
            if key.strip().lower() == "charset":
                charset = value.strip().strip('"')
                if charset:
                    return charset
        return None

    @property
    async def apparent_encoding(self):
        """The apparent encoding, detected automatically. Must be awaited.

        Valid UTF-8 bodies (the overwhelmingly common case) are recognized
        with a cheap strict decode. Otherwise, uses chardet for detection if
        installed (off the event loop, since detection is CPU-bound), falling
        back to UTF-8.
        """
        declared_encoding = await self.declared_encoding

        if declared_encoding:
            return declared_encoding

        content = await self.content
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            return DEFAULT_ENCODING

        if chardet is not None:
            detected = await run_in_threadpool(chardet.detect, content)
            return detected["encoding"] or DEFAULT_ENCODING

        return DEFAULT_ENCODING

    @property
    def is_secure(self) -> bool:
        """``True`` if the request was made over HTTPS."""
        return self.url.scheme == "https"

    def accepts(self, content_type: str) -> bool:
        """Whether the client's ``Accept`` header allows ``content_type``.

        Honors media ranges (``*/*``, ``type/*``) and q-values. Per RFC 9110
        §12.5.1 the *most specific* matching range governs, so an explicit
        ``q=0`` range (not acceptable) excludes the type even when a broader
        range (``*/*`` or ``type/*``) would match. An absent ``Accept`` header
        accepts anything. ``content_type`` may be a full media type
        (``application/json``) or a bare subtype token (``json``).
        """
        return self._accept_quality(content_type) > 0

    @property
    def _parsed_accept(self):
        """The parsed ``Accept`` header, cached: ``(header_present, ranges)``."""
        if self._accept is None:
            accept = self.headers.get("Accept")
            self._accept = (bool(accept), _parse_accept(accept) if accept else [])
        return self._accept

    def _accept_quality(self, content_type: str) -> float:
        """The q-value the ``Accept`` header assigns to ``content_type``.

        The most specific matching media range governs (RFC 9110 §12.5.1).
        Returns ``1.0`` when the request carries no ``Accept`` header, and
        ``0.0`` when no range matches (or the governing match says ``q=0``).
        """
        has_header, ranges = self._parsed_accept
        if not has_header:
            return 1.0
        wanted = content_type.lower()
        best_specificity = -1
        best_quality = 0.0
        for type_, subtype, quality in ranges:
            if "/" in wanted:
                ctype, _, csubtype = wanted.partition("/")
                if type_ not in ("*", ctype) or subtype not in ("*", csubtype):
                    continue
                specificity = (type_ != "*") + (subtype != "*")
            # Bare token (e.g. "json"): match a wildcard range or a subtype/type
            # that contains the token (keeps the historical substring behavior).
            elif wanted in subtype or wanted in type_:
                specificity = 2
            elif subtype == "*":
                # A bare token's top-level type is unknown, so a type-specific
                # wildcard (``audio/*``) is no more specific than ``*/*`` for
                # it — its q=0 must not veto a token another range allows.
                specificity = 0
            else:
                continue
            if specificity > best_specificity:
                best_specificity = specificity
                best_quality = quality
            elif specificity == best_specificity:
                # Among equally specific ranges, be generous: any q>0 wins.
                best_quality = max(best_quality, quality)
        return best_quality

    def preferred_media_type(self, candidates):
        """Pick the candidate media type the client's ``Accept`` header prefers.

        Candidates are ranked by the q-value of the most specific matching
        media range (RFC 9110 §12.5.1); ties — and requests without an
        ``Accept`` header — keep the order of ``candidates``, so put the
        server's preferred default first. Returns ``None`` when the client
        accepts none of them.

        Usage::

            preferred = req.preferred_media_type(
                ["application/json", "text/csv"]
            )

        :param candidates: An iterable of media types (``application/json``)
            or bare subtype tokens (``json``).
        """
        best = None
        best_quality = 0.0
        for candidate in candidates:
            quality = self._accept_quality(candidate)
            if quality > best_quality:
                best, best_quality = candidate, quality
        return best

    async def media(self, format: str | Callable | None = None) -> Any:  # noqa: A002
        """Renders incoming json/yaml/form data as Python objects. Must be awaited.

        :param format: The name of the format being used.
                       Alternatively, accepts a custom callable for the format type.
        """

        if format is None:
            # Media types are case-insensitive (RFC 7231 §3.1.1.1).
            mimetype = self.mimetype.lower()
            if "msgpack" in mimetype:
                format = "msgpack"  # noqa: A001
            elif "yaml" in mimetype:
                format = "yaml"  # noqa: A001
            elif "form" in mimetype:
                format = "form"  # noqa: A001
            else:
                format = "json"  # noqa: A001

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

    def media_sync(self, format: str | Callable | None = None) -> Any:  # noqa: A002
        """Synchronous :meth:`media`, for use from **sync** handlers.

        Responder runs sync handlers in a worker thread, so this safely bridges
        to the event loop to read and parse the body. Calling it from an
        ``async`` handler raises — use ``await req.media()`` there instead.

        Usage::

            @api.route("/", methods=["POST"])
            def create(req, resp):
                data = req.media_sync()
        """
        import anyio

        return anyio.from_thread.run(self.media, format)

    @property
    def text_sync(self):
        """Synchronous :attr:`text`, for use from **sync** handlers (see
        :meth:`media_sync`)."""
        import anyio

        async def _get():
            return await self.text

        return anyio.from_thread.run(_get)


class RangeNotSatisfiable(Exception):
    """The request's ``Range`` header cannot be satisfied (→ 416)."""


_MAX_BYTE_RANGES = 16


def _parse_byte_range(header, size):
    """Parse a ``Range`` header against a resource of ``size``.

    Returns a list of ``(start, end)`` inclusive ranges, ``None`` when the
    header is absent or malformed (serve the full resource), or raises
    :class:`RangeNotSatisfiable` (→ 416).
    """
    if not header or not header.startswith("bytes=") or size == 0:
        return None
    range_specs = header[len("bytes=") :]
    if range_specs.count(",") >= _MAX_BYTE_RANGES:
        return None
    specs = [part.strip() for part in range_specs.split(",")]
    if not specs or any(not spec for spec in specs):
        return None

    ranges = []
    for spec in specs:
        start_s, sep, end_s = spec.partition("-")
        if not sep:
            return None
        try:
            if not start_s:  # Suffix range: bytes=-N (the last N bytes).
                suffix = int(end_s)
                if suffix <= 0:
                    raise RangeNotSatisfiable()
                start, end = max(0, size - suffix), size - 1
            else:
                start = int(start_s)
                end = min(int(end_s), size - 1) if end_s else size - 1
        except ValueError:
            return None  # Malformed numbers: ignore the header.

        if start >= size or start > end:
            raise RangeNotSatisfiable()
        ranges.append((start, end))

    ranges.sort()
    merged: list[tuple[int, int]] = []
    for start, end in ranges:
        if not merged or start > merged[-1][1] + 1:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))

    return merged


def _multipart_range_header(boundary, content_type, start, end, size):
    return (
        f"--{boundary}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Range: bytes {start}-{end}/{size}\r\n"
        "\r\n"
    ).encode("ascii")


def _md5_hex(raw: bytes) -> str:
    """Hex MD5 digest (not security-sensitive — an ETag)."""
    return hashlib.md5(raw, usedforsecurity=False).hexdigest()


def _strong_etag_core(tag):
    """The quoted core of a strong entity-tag, or ``None`` for weak/invalid."""
    if not tag:
        return None
    tag = tag.strip()
    if tag.startswith("W/"):
        return None
    if len(tag) >= 2 and tag.startswith('"') and tag.endswith('"'):
        return tag
    return None


def _weak_etag_core(tag):
    """The core of an entity-tag, ignoring a weak ``W/`` prefix (RFC 9110 §8.8.3.2)."""
    return tag[2:] if tag.startswith("W/") else tag


def _folded_header(headers, key):
    """Fold repeated ``key`` lines into one comma-joined value, or ``None``.

    RFC 9110 §5.2 treats multiple lines of a list-valued header (``If-Match``,
    ``If-None-Match``, …) as equivalent to a single line whose values are
    comma-joined. Reading with single-value ``get`` would see only the last
    line, so precondition checks must fold every line first.
    """
    values = headers.get_list(key)
    if not values:
        return None
    return ", ".join(values)


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
    """Whether ``location`` points off-site (absolute, or protocol-relative).

    Browsers normalize backslashes to forward slashes and ignore leading
    control/whitespace when resolving a ``Location``, so ``/\\evil.com`` and
    friends are treated as protocol-relative — normalize before classifying.
    """
    norm = location.replace("\\", "/").lstrip(" \t\r\n\x00")
    if norm.startswith("//"):
        return True
    parsed = urlparse(norm)
    return bool(parsed.scheme or parsed.netloc)


# RFC 7230 token characters — safe to place bare inside a quoted-string.
_TOKEN_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!#$%&'*+-.^_`|~"
)


def _content_disposition_attachment(name: str) -> str:
    """Build a safe ``Content-Disposition: attachment`` header value.

    Strips characters that could terminate the header or the quoted-string
    (CR, LF, NUL), backslash-escapes ``"`` and ``\\`` in the quoted-string,
    and adds the RFC 5987 ``filename*=`` form whenever the name contains
    characters outside the HTTP token set (non-ASCII names get only
    ``filename*=``, as before).
    """
    from urllib.parse import quote

    name = name.replace("\r", "").replace("\n", "").replace("\x00", "")
    if name and all(c in _TOKEN_CHARS for c in name):
        return f'attachment; filename="{name}"'
    if name.isascii():
        escaped = name.replace("\\", "\\\\").replace('"', '\\"')
        return f'attachment; filename="{escaped}"; filename*=UTF-8\'\'{quote(name)}'
    return f"attachment; filename*=UTF-8''{quote(name)}"


def _merge_header_tokens(*values):
    """Merge comma-separated header tokens, de-duped case-insensitively, in order."""
    seen: dict[str, str] = {}
    for value in values:
        if not value:
            continue
        for token in value.split(","):
            token = token.strip()
            if token and token.lower() not in seen:
                seen[token.lower()] = token
    return ", ".join(seen.values())


def _scrub_header_value(value: object) -> object:
    """Strip CR/LF/NUL from a response header value to prevent header injection.

    A bare CR or LF in a header value would let attacker-controlled data (e.g. a
    user-supplied ``Location`` in :meth:`Response.redirect`) terminate the header
    and inject additional headers or split the response — the classic response-
    splitting vector. Bare CR/LF are never valid inside an HTTP field value
    (RFC 9110 §5.5), so stripping them changes no legitimate response. Non-string
    values (which Starlette rejects downstream anyway) pass through untouched.
    """
    if isinstance(value, str):
        return value.replace("\r", "").replace("\n", "").replace("\x00", "")
    return value


def _sse_single_line(value: object) -> str:
    """Scrub an SSE single-line field (``event``/``id``/``retry``).

    These fields are terminated by a line break, so a CR or LF in the value
    would end the field early and let caller-supplied content inject extra SSE
    lines or whole frames. Strip line breaks (and NUL, which the spec forbids
    in ``id``).
    """
    return str(value).replace("\r", "").replace("\n", "").replace("\x00", "")


def _sse_frame(data=None, *, event=None, id=None, retry=None):  # noqa: A002
    """Encode one Server-Sent Events frame. dict/list data is JSON-encoded."""
    lines = []
    if event is not None:
        lines.append(f"event: {_sse_single_line(event)}")
    if id is not None:
        lines.append(f"id: {_sse_single_line(id)}")
    if retry is not None:
        lines.append(f"retry: {_sse_single_line(retry)}")
    if data is not None:
        if isinstance(data, (dict, list)):
            data = json.dumps(data)
        # Split on any SSE line terminator (\r\n, \r, or \n) so multi-line data
        # becomes multiple `data:` lines and a lone \r can't inject a new field.
        normalized = str(data).replace("\r\n", "\n").replace("\r", "\n")
        for line in normalized.split("\n"):
            lines.append(f"data: {line}")
    lines.append("")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def _format_sse_event(event: Any) -> bytes:
    """Turn a yielded SSE event (str / bytes / dict) into wire bytes."""
    if isinstance(event, (bytes, bytearray)):
        return bytes(event)
    if isinstance(event, dict):
        if set(event) == {"comment"}:
            return f": {_sse_single_line(event['comment'])}\n\n".encode()
        return _sse_frame(
            data=event.get("data"),
            event=event.get("event"),
            id=event.get("id"),
            retry=event.get("retry"),
        )
    return _sse_frame(data=event)


async def _sse_with_heartbeat(source, interval, maxsize=1024):
    """Yield from ``source``, injecting a heartbeat comment after ``interval``
    seconds of silence — without cancelling the producer mid-item (a background
    task feeds a queue; only the idle ``queue.get`` is timed out).

    The queue is bounded (``maxsize``): if the client can't keep up, the
    producer's ``queue.put`` blocks rather than buffering events without limit,
    restoring backpressure for slow/stalled consumers."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    done = object()

    async def pump():
        try:
            async for item in source:
                await queue.put((False, item))
        except Exception as exc:  # surface a producer error to the consumer
            await queue.put((True, exc))
            return
        await queue.put((False, done))

    task = asyncio.ensure_future(pump())
    try:
        while True:
            try:
                is_exc, item = await asyncio.wait_for(queue.get(), interval)
            except asyncio.TimeoutError:
                yield {"comment": "keepalive"}
                continue
            if is_exc:
                raise item
            if item is done:
                return
            yield item
    finally:
        task.cancel()
        try:
            await task
        except BaseException:  # noqa: BLE001, S110 - cleanup of cancelled pump
            pass


async def _empty_async_body():
    chunks: tuple[bytes, ...] = ()
    for chunk in chunks:
        yield chunk


def content_setter(mimetype):
    def getter(instance):
        return instance.content

    def setter(instance, value):
        # Assigning resp.text/resp.html *replaces* the body: discard any
        # previously scheduled file/stream and its framing headers (e.g. the
        # Content-Length that resp.file() computed), or the new body goes out
        # with the old body's length.
        instance._reset_body()
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
    :var headers: A case-insensitive (case-preserving) dict of response headers.
    :var cookies: A ``SimpleCookie`` holding cookies to set on the response.
    :var session: A dict of session data. Changes are persisted in a signed cookie.
    :var etag: Entity tag for the response. When the request's ``If-None-Match`` matches on ``GET``/``HEAD``, an automatic ``304 Not Modified`` is sent instead of the body. On state-changing methods, ``If-Match`` / ``If-None-Match`` preconditions are enforced against it with an automatic ``412 Precondition Failed``.
    :var last_modified: A ``datetime`` (or HTTP-date string) for ``Last-Modified``. Honors ``If-Modified-Since`` with automatic ``304`` responses, and ``If-Unmodified-Since`` on state-changing methods with automatic ``412`` responses.
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
        "mimetype",
        "etag",
        "last_modified",
        "_stream",
        "_auto_etag",
        "_auto_vary",
        "_background",
        "_deferred_content",
        "_multipart_range_boundary",
        "_multipart_range_content_type",
    ]

    text = content_setter("text/plain")
    html = content_setter("text/html")

    def __init__(self, req, *, formats, auto_etag=False, auto_vary=False):
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
        self._auto_vary = auto_vary
        self._background = None
        self._deferred_content = None
        self._multipart_range_boundary = None
        self._multipart_range_content_type = None
        self.headers: CaseInsensitiveDict = CaseInsensitiveDict()
        self.formats = formats
        self.cookies: SimpleCookie = SimpleCookie()

    @property
    def session(self):
        """The session dict (delegates to the request; requires sessions on)."""
        return self.req.session

    @session.setter
    def session(self, value):
        scope = self.req._starlette.scope
        if "session" not in scope:
            raise RuntimeError("Cannot assign resp.session: sessions are disabled.")
        # Mutate in place — rebinding would replace Starlette's Session subclass
        # (which tracks accessed/modified) with a plain dict and crash the
        # cookie middleware. clear()+update() works for the server backend too.
        session = scope["session"]
        session.clear()
        session.update(value)

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
        if not inspect.isasyncgenfunction(func):
            raise TypeError(
                "resp.stream() requires an async generator function "
                f"(defined with 'async def' and 'yield'); got {func!r}"
            )

        self._stream = functools.partial(func, *args, **kwargs)

        return func

    def sse(self, func=None, *args, heartbeat=None, **kwargs):
        """Set up a Server-Sent Events response from an async generator.

        Each yielded value becomes one event:

        - a ``dict`` with any of ``data``, ``event``, ``id``, ``retry``
          (``data`` that is a ``dict``/``list`` is JSON-encoded);
        - ``{"comment": "..."}`` for a comment/keepalive line;
        - a ``str`` (or anything else) is sent as the ``data`` field;
        - ``bytes`` are written verbatim (pre-formatted frame).

        Usage::

            @api.route("/events")
            async def events(req, resp):
                @resp.sse
                async def stream():
                    for i in range(10):
                        yield {"data": {"n": i}, "id": i}

        Pass ``heartbeat=`` (seconds) to emit a keepalive comment during idle
        periods, so proxies don't drop the connection::

            @resp.sse(heartbeat=15)
            async def stream(): ...

        On reconnect the client sends the last id it saw; read it with
        :attr:`Request.last_event_id`.

        :param func: The async generator function (or omit when using the
            ``@resp.sse(heartbeat=...)`` form).
        :param heartbeat: Idle keepalive interval in seconds (``None`` = off).
        """
        if func is None:  # called as @resp.sse(heartbeat=...) -> decorator
            def decorator(f):
                return self.sse(f, *args, heartbeat=heartbeat, **kwargs)

            return decorator

        if not inspect.isasyncgenfunction(func):
            raise TypeError(
                "resp.sse() requires an async generator function "
                f"(defined with 'async def' and 'yield'); got {func!r}"
            )

        async def sse_generator():
            source = func(*args, **kwargs)
            if heartbeat:
                source = _sse_with_heartbeat(source, heartbeat)
            async for event in source:
                yield _format_sse_event(event)

        self._stream = sse_generator
        self.mimetype = "text/event-stream"
        self.headers["Cache-Control"] = "no-cache"
        self.headers["Connection"] = "keep-alive"
        # Tell nginx & friends not to buffer the stream, so events flush live.
        self.headers["X-Accel-Buffering"] = "no"

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

        Sets ``Accept-Ranges``, and on satisfiable ranges, the ``206`` status
        plus either ``Content-Range`` or a multipart byte-range body.
        Unsatisfiable ranges raise
        :class:`RangeNotSatisfiable` after marking the response ``416``.
        """
        self.headers["Accept-Ranges"] = "bytes"
        if self.req.method not in ("GET", "HEAD"):
            return None
        if not self._if_range_matches():
            return None

        try:
            byte_ranges = _parse_byte_range(self.req.headers.get("Range"), size)
        except RangeNotSatisfiable:
            self.status_code = 416
            self.headers["Content-Range"] = f"bytes */{size}"
            self.content = b""
            raise

        if byte_ranges is None:
            return None

        self.status_code = 206
        if len(byte_ranges) == 1:
            start, end = byte_ranges[0]
            self.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            return byte_ranges

        boundary = hashlib.md5(  # noqa: S324 - boundary, not security-sensitive
            self.req.headers.get("Range", "").encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()
        self._multipart_range_boundary = f"responder-{boundary}"
        self._multipart_range_content_type = self.mimetype or "application/octet-stream"
        self.mimetype = f"multipart/byteranges; boundary={self._multipart_range_boundary}"
        self.headers.pop("Content-Range", None)
        return byte_ranges

    def _if_range_matches(self):
        """Whether a ``Range`` request is allowed to stay partial.

        ``If-Range`` turns a stale range request back into a full-body response:
        the range is honored only when the provided validator still matches the
        current representation. If it doesn't, the ``Range`` header is ignored.
        """
        if_range = self.req.headers.get("If-Range")
        if not if_range or not self.req.headers.get("Range"):
            return True

        request_tag = _strong_etag_core(if_range)
        if request_tag is not None:
            current_tag = _strong_etag_core(self._normalized_etag) if self.etag else None
            return current_tag is not None and current_tag == request_tag

        if self.last_modified is None:
            return False
        try:
            current = parsedate_to_datetime(self._last_modified_header)
            candidate = parsedate_to_datetime(if_range)
            if current is None or candidate is None:
                return False
            if current.tzinfo is None:
                current = current.replace(tzinfo=UTC)
            if candidate.tzinfo is None:
                candidate = candidate.replace(tzinfo=UTC)
            return current <= candidate
        except (TypeError, ValueError):
            return False

    def _apply_file_conditionals(self, stat_result):
        """Set a stat-based weak ETag and Last-Modified (unless already set), so
        the existing 304 machinery answers ``If-None-Match`` / ``If-Modified-Since``
        for served files without reading their contents."""
        if self.etag is None:
            mtime_ns = getattr(
                stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000)
            )
            self.etag = f'W/"{stat_result.st_size:x}-{mtime_ns:x}"'
        if self.last_modified is None:
            self.last_modified = datetime.fromtimestamp(
                stat_result.st_mtime, tz=UTC
            )

    def _ranges_content_length(self, byte_ranges, size):
        """The exact body byte count for the ranges served from a ``size`` file
        (single/full range, or a multipart/byteranges body)."""
        if len(byte_ranges) == 1:
            start, end = byte_ranges[0]
            return end - start + 1 if size else 0
        boundary = self._multipart_range_boundary
        content_type = self._multipart_range_content_type or "application/octet-stream"
        total = 0
        for start, end in byte_ranges:
            total += len(
                _multipart_range_header(boundary, content_type, start, end, size)
            )
            total += end - start + 1
            total += 2  # the CRLF terminating each part
        total += len(f"--{boundary}--\r\n")
        return total

    def stream_file(
        self, path, *, content_type=None, chunk_size=8192, root=None, conditional=True
    ):
        """Stream a file without loading it entirely into memory.

        Supports HTTP range requests (``Range: bytes=...``) with ``206``
        partial responses, enabling video seeking and resumable downloads.

        :param path: Path to the file.
        :param content_type: Optional MIME type override.
        :param chunk_size: Size of chunks to read (default 8192 bytes).
        :param root: If given, ``path`` is resolved under this directory and any
                     attempt to escape it (via ``..`` or a symlink) yields a
                     ``404`` — use this whenever ``path`` is built from user input.
        :param conditional: If ``True`` (default), set a stat-based ETag and
                     ``Last-Modified`` so conditional requests get a ``304``.
        """
        from pathlib import Path as PathType

        path = PathType(path) if root is None else _resolve_within(path, root)
        self._set_file_mimetype(path, content_type)

        stat_result = path.stat()
        size = stat_result.st_size
        if conditional:
            self._apply_file_conditionals(stat_result)
            # Evaluate the validator before Range: a matching conditional must
            # win (304), not be masked by a 206 partial response.
            if self._is_not_modified():
                return
        try:
            byte_range = self._requested_range(size)
        except RangeNotSatisfiable:
            return
        byte_ranges = byte_range if byte_range else [(0, size - 1)]
        # The byte count is known exactly, so advertise it — download
        # managers and browsers can show progress and verify resumes,
        # and HEAD requests report the size.
        self.headers["Content-Length"] = str(
            self._ranges_content_length(byte_ranges, size)
        )

        async def file_generator():
            import anyio

            async with await anyio.open_file(path, "rb") as f:
                if len(byte_ranges) > 1:
                    boundary = self._multipart_range_boundary
                    assert boundary is not None
                    content_type = (
                        self._multipart_range_content_type
                        or "application/octet-stream"
                    )
                    for start, end in byte_ranges:
                        yield _multipart_range_header(
                            boundary, content_type, start, end, size
                        )
                        await f.seek(start)
                        remaining = end - start + 1
                        while remaining > 0:
                            chunk = await f.read(min(chunk_size, remaining))
                            if not chunk:
                                break
                            remaining -= len(chunk)
                            yield chunk
                        yield b"\r\n"
                    yield f"--{boundary}--\r\n".encode("ascii")
                    return

                start, end = byte_ranges[0]
                remaining = end - start + 1 if size else 0
                if start:
                    await f.seek(start)
                while remaining > 0:
                    chunk = await f.read(min(chunk_size, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        self._stream = file_generator

    def file(self, path, *, content_type=None, root=None, conditional=True):
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
        :param conditional: If ``True`` (default), set a stat-based ETag and
                     ``Last-Modified`` so conditional requests get a ``304``
                     (and the file's bytes aren't read to compute it).
        """
        from pathlib import Path

        path = Path(path) if root is None else _resolve_within(path, root)
        self._set_file_mimetype(path, content_type)

        stat_result = path.stat()
        size = stat_result.st_size
        if conditional:
            self._apply_file_conditionals(stat_result)
            # Evaluate the validator before Range: a matching conditional must
            # win (304), not be masked by a 206 partial response.
            if self._is_not_modified():
                return
        try:
            byte_range = self._requested_range(size)
        except RangeNotSatisfiable:
            return

        byte_ranges = byte_range if byte_range else [(0, size - 1)]
        # Known exactly from the stat/ranges — lets HEAD report the size
        # without reading the file.
        self.headers["Content-Length"] = str(
            self._ranges_content_length(byte_ranges, size)
        )

        def _read() -> bytes:
            if not size:
                return b""
            with path.open("rb") as f:
                if len(byte_ranges) > 1:
                    boundary = self._multipart_range_boundary
                    assert boundary is not None
                    content_type = (
                        self._multipart_range_content_type
                        or "application/octet-stream"
                    )
                    parts = []
                    for start, end in byte_ranges:
                        parts.append(
                            _multipart_range_header(
                                boundary, content_type, start, end, size
                            )
                        )
                        f.seek(start)
                        parts.append(f.read(end - start + 1))
                        parts.append(b"\r\n")
                    parts.append(f"--{boundary}--\r\n".encode("ascii"))
                    return b"".join(parts)

                start, end = byte_ranges[0]
                if start:
                    f.seek(start)
                return f.read(end - start + 1)

        async def _deferred() -> bytes:
            return await run_in_threadpool(_read)

        self._deferred_content = _deferred

    def download(
        self, path, *, filename=None, content_type=None, root=None, conditional=True
    ):
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

        path = Path(path) if root is None else _resolve_within(path, root)
        name = filename or path.name

        self.stream_file(path, content_type=content_type, conditional=conditional)
        self.headers["Content-Disposition"] = _content_disposition_attachment(name)

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

    def reset_for_error(self):
        """Discard a previously prepared success response before framework errors."""
        self.content = None
        self.media = None
        self.mimetype = None
        self._stream = None
        self._deferred_content = None
        self._background = None
        self._multipart_range_boundary = None
        self._multipart_range_content_type = None
        self.etag = None
        self.last_modified = None
        self.headers.clear()
        self.cookies.clear()

    def _reset_body(self) -> None:
        self.content = None
        self.media = None
        self.mimetype = None
        self._stream = None
        self._deferred_content = None
        self._multipart_range_boundary = None
        self._multipart_range_content_type = None
        # file()/stream_file() write body-derived framing headers; a replaced
        # body must not inherit them (a stale Content-Length breaks HTTP
        # framing — h11 rejects the short body, curl hangs waiting for it).
        self.headers.pop("Content-Length", None)
        self.headers.pop("Content-Range", None)
        self.headers.pop("Accept-Ranges", None)

    def created(self, media=None, *, location=None, headers=None):
        """Mark the response as ``201 Created``.

        Optionally set a JSON-serializable body and a ``Location`` header::

            resp.created({"id": item.id}, location=f"/items/{item.id}")
        """
        self.status_code = 201
        if media is not None:
            self._reset_body()
            self.media = media
        if location is not None:
            self.headers["Location"] = str(location)
        if headers:
            self.headers.update(headers)

    def no_content(self, *, headers=None):
        """Mark the response as ``204 No Content`` and clear any response body."""
        self.status_code = 204
        self._reset_body()
        self.content = b""
        self.headers.pop("Content-Type", None)
        if headers:
            self.headers.update(headers)

    @property
    def _json_default_hook(self):
        """The ``json.dumps`` ``default=`` hook the API's json format uses
        (built-in conversions composed with any user ``API(encoder=...)``),
        so ad-hoc JSON emitters serialize the same types the media path does."""
        json_format = self.formats.get("json") if self.formats else None
        hook = getattr(json_format, "_responder_default_hook", None)
        if hook is not None:
            return hook
        from .formats import _json_default

        return _json_default

    def problem(
        self,
        status_code,
        detail=None,
        *,
        title=None,
        type=None,  # noqa: A002
        instance=None,
        errors=None,
        headers=None,
        **extensions,
    ):
        """Send an RFC 9457 ``application/problem+json`` response.

        API-level ``problem_handler`` enrichment and request IDs are applied,
        then explicit helper arguments and extension members are layered on top.
        """
        self.status_code = int(status_code)
        payload = problem_payload_for(
            self.req._starlette.scope,
            self.status_code,
            detail,
            title=title,
            errors=errors,
            request=self.req,
        )
        if type is not None:
            payload["type"] = type
        if instance is not None:
            payload["instance"] = instance
        payload.update(extensions)

        self._reset_body()
        self.content = json.dumps(payload, default=self._json_default_hook).encode(
            "utf-8"
        )
        self.mimetype = PROBLEM_JSON
        self.headers["Content-Type"] = PROBLEM_JSON
        if headers:
            self.headers.update(headers)

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
        self,
        location: str,
        *,
        set_text: bool = True,
        status_code: int | None = None,
        permanent: bool = False,
        allow_external: bool = True,
    ) -> None:
        """Redirect the client to a different URL.

        :param location: The URL to redirect to.
        :param set_text: If ``True``, set a default redirect message as the body.
        :param status_code: An explicit HTTP status code. When omitted, the
                            redirect is a method-preserving ``307 Temporary
                            Redirect`` (or ``308 Permanent Redirect`` with
                            ``permanent=True``).
        :param permanent: If ``True``, send a ``308 Permanent Redirect``
                          instead of the default ``307``. Ignored when an
                          explicit ``status_code`` is given.
        :param allow_external: If ``False``, refuse (with a ``400``) to redirect
                               to an absolute or protocol-relative URL — pass
                               this whenever ``location`` comes from user input,
                               to prevent open redirects.
        """
        if not allow_external and _is_external_url(location):
            raise HTTPException(
                status_code=400, detail="Refusing to redirect to an external URL"
            )
        if status_code is None:
            status_code = HTTP_308 if permanent else HTTP_307
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
                content_type = self.mimetype
                # Text responses declare their charset the standard way, in
                # Content-Type (rather than via a nonstandard header) — but
                # only when the framework itself encodes a str body. Raw
                # bytes (e.g. from resp.file()) have an unknown encoding, so
                # they go out with the bare media type unless the user set a
                # charset explicitly.
                if (
                    isinstance(content, str)
                    and content_type.startswith("text/")
                    and self.encoding is not None
                    and "charset=" not in content_type.lower()
                ):
                    content_type = f"{content_type}; charset={self.encoding}"
                headers["Content-Type"] = content_type
            # Encode str bodies with resp.encoding uniformly, so e.g. an
            # encoding='latin-1' HTML body isn't UTF-8-encoded downstream.
            if isinstance(content, str) and self.encoding is not None:
                content = content.encode(self.encoding)
            return (content, headers)

        # Try formats in the client's q-ranked Accept preference order
        # (RFC 9110 §12.5.1). Ties — including the no-Accept-header case,
        # where every format scores 1.0 — keep registration order, so JSON
        # stays the default.
        qualities = {name: self.req._accept_quality(name) for name in self.formats}
        for format_ in sorted(self.formats, key=lambda name: -qualities[name]):
            if qualities[format_] <= 0:
                break
            encoded = await self.formats[format_](self, encode=True)
            # Formats that can't encode (e.g. form, files) return None.
            if encoded is not None:
                return encoded, ({"Vary": "Accept"} if self._auto_vary else {})

        # Default to JSON anyway.
        headers = {"Content-Type": "application/json"}
        if self._auto_vary:
            headers["Vary"] = "Accept"
        return (await self.formats["json"](self, encode=True), headers)

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

    def delete_cookie(
        self,
        key,
        *,
        path="/",
        domain=None,
        secure=False,
        httponly=True,
        samesite="lax",
    ):
        """Expire a cookie on the client (empty value, ``Max-Age=0``).

        Pass the same ``path``/``domain`` the cookie was set with so the browser
        matches and drops it.

        Usage::

            resp.delete_cookie("token")

        """
        self.set_cookie(
            key,
            value="",
            max_age=0,
            expires="Thu, 01 Jan 1970 00:00:00 GMT",
            path=path,
            domain=domain,
            secure=secure,
            httponly=httponly,
            samesite=samesite,
        )

    def vary(self, *values):
        """Add one or more field names to the response ``Vary`` header.

        Tokens are merged with any existing ``Vary`` value and de-duplicated
        case-insensitively::

            resp.vary("Accept", "Accept-Language")

        """
        merged = _merge_header_tokens(self.headers.get("Vary"), *values)
        if merged:
            self.headers["Vary"] = merged

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
                value = value.replace(tzinfo=UTC)
            return format_datetime(value, usegmt=True)
        return str(self.last_modified)

    def _is_not_modified(self):
        """Whether the request's conditional headers match this response."""
        if self.req.method not in ("GET", "HEAD"):
            return False
        if self.status_code not in (None, 200):
            return False

        # If-None-Match takes precedence over If-Modified-Since (RFC 7232).
        if_none_match = _folded_header(self.req.headers, "If-None-Match")
        if if_none_match and self.etag is not None:
            return self._if_none_match_matches(if_none_match)

        if_modified_since = self.req.headers.get("If-Modified-Since")
        if if_modified_since and self.last_modified is not None:
            try:
                since = parsedate_to_datetime(if_modified_since)
                current = parsedate_to_datetime(self._last_modified_header)
                # A "-0000" zone parses to a naive datetime; normalize both to
                # UTC so comparing them can't raise (naive vs aware -> TypeError).
                if since.tzinfo is None:
                    since = since.replace(tzinfo=UTC)
                if current.tzinfo is None:
                    current = current.replace(tzinfo=UTC)
                return current <= since
            except (TypeError, ValueError):
                return False

        return False

    def _if_none_match_matches(self, if_none_match):
        """Whether ``If-None-Match`` matches the current ETag (weak comparison)."""
        if if_none_match.strip() == "*":
            return True
        tags = [_weak_etag_core(t.strip()) for t in if_none_match.split(",")]
        return _weak_etag_core(self._normalized_etag) in tags

    def _precondition_failed(self):
        """Whether a state-changing request's preconditions fail (→ 412).

        Evaluates the RFC 9110 §13 write-side preconditions — ``If-Match``,
        ``If-Unmodified-Since``, and ``If-None-Match`` on non-GET/HEAD
        methods — against the validators the handler set (``resp.etag`` /
        ``resp.last_modified``), in the order §13.2.2 prescribes. Like
        :meth:`_is_not_modified`, a precondition is only evaluated when the
        corresponding validator is set, and only for responses that would
        otherwise succeed (2xx).
        """
        if self.req.method in ("GET", "HEAD"):
            return False
        # Per RFC 9110 §13.2.1, ignore preconditions when the response would
        # not be a 2xx (or 412) anyway.
        code = self.status_code
        if code is not None and code != 412 and not (200 <= code < 300):
            return False

        # 1. If-Match: strong comparison (RFC 9110 §13.1.1) — a weak ETag
        #    can never match. "*" succeeds because a set resp.etag means a
        #    current representation exists.
        if_match = _folded_header(self.req.headers, "If-Match")
        if if_match:
            if self.etag is not None and if_match.strip() != "*":
                current = _strong_etag_core(self._normalized_etag)
                tags = [_strong_etag_core(t.strip()) for t in if_match.split(",")]
                if current is None or current not in tags:
                    return True
        # 2. If-Unmodified-Since: only when If-Match is absent.
        elif self.last_modified is not None:
            if_unmodified_since = self.req.headers.get("If-Unmodified-Since")
            if if_unmodified_since:
                try:
                    since = parsedate_to_datetime(if_unmodified_since)
                    current_dt = parsedate_to_datetime(self._last_modified_header)
                    # Normalize naive datetimes ("-0000" zones) to UTC so the
                    # comparison can't raise (naive vs aware -> TypeError).
                    if since.tzinfo is None:
                        since = since.replace(tzinfo=UTC)
                    if current_dt.tzinfo is None:
                        current_dt = current_dt.replace(tzinfo=UTC)
                    if current_dt > since:
                        return True
                except (TypeError, ValueError):
                    pass  # An invalid HTTP-date isn't a usable precondition.

        # 3. If-None-Match on a state-changing method: a match is 412, not 304.
        if_none_match = _folded_header(self.req.headers, "If-None-Match")
        if if_none_match and self.etag is not None:
            return self._if_none_match_matches(if_none_match)

        return False

    async def __call__(self, scope, receive, send):
        body = None
        headers: dict = {}
        built = False

        # Neutralize header-injection: strip CR/LF/NUL from every response
        # header value before it reaches Starlette's raw_headers. This is the
        # single choke point for the not-modified (304), precondition-failed
        # (412), and main response paths, all of which build from self.headers.
        for _key in list(self.headers):
            self.headers[_key] = _scrub_header_value(self.headers[_key])

        if (
            self._auto_etag
            and self.etag is None
            and self._stream is None
            and self.req.method in ("GET", "HEAD")
            and self.status_code in (None, 200)
        ):
            body, headers = await self.body
            built = True
            raw = (
                body
                if isinstance(body, bytes)
                else str(body).encode(self.encoding or DEFAULT_ENCODING)
            )
            # Hash off the event loop for large bodies so a big auto-etagged
            # response doesn't block the loop; small bodies stay inline.
            if len(raw) > 65536:
                self.etag = await run_in_threadpool(_md5_hex, raw)
            else:
                self.etag = _md5_hex(raw)

        if self.etag is not None or self.last_modified is not None:
            if self.etag is not None:
                self.headers["ETag"] = _scrub_header_value(self._normalized_etag)
            if self.last_modified is not None:
                self.headers["Last-Modified"] = _scrub_header_value(
                    self._last_modified_header
                )

            if self._is_not_modified():
                # Carry the negotiated Vary onto the 304 too, else a shared
                # cache keys the not-modified response without it.
                if built and headers.get("Vary"):
                    self.vary(headers["Vary"])
                elif (
                    self._auto_vary
                    and self._stream is None
                    and self.content is None
                    and self._deferred_content is None
                ):
                    self.vary("Accept")
                not_modified = StarletteResponse(
                    status_code=304, headers=self.headers, background=self._background
                )
                self._prepare_cookies(not_modified)
                await not_modified(scope, receive, send)
                return

            if self._precondition_failed():
                # The headers already carry the current ETag/Last-Modified,
                # so the client can see the validator its precondition lost to.
                precondition_failed = StarletteResponse(
                    status_code=412, headers=self.headers, background=self._background
                )
                self._prepare_cookies(precondition_failed)
                await precondition_failed(scope, receive, send)
                return

        if not built:
            # HEAD discards the body, so skip a deferred whole-file read
            # (resp.file()) entirely — the headers (including Content-Length,
            # already computed from the stat) are all that's needed.
            if (
                self.req.method == "HEAD"
                and self._deferred_content is not None
                and self.content is None
            ):
                self.content = b""
            body, headers = await self.body
        if self.headers:
            # Merge Vary from both sources so an explicit resp.vary(...) and an
            # auto-added "Accept" combine rather than clobber each other.
            vary = _merge_header_tokens(headers.get("Vary"), self.headers.get("Vary"))
            # Merge case-insensitively, so a handler's 'content-type' replaces
            # the framework's 'Content-Type' instead of emitting both.
            merged = CaseInsensitiveDict(headers)
            merged.update(self.headers)
            headers = merged
            if vary:
                headers["Vary"] = vary

        response_cls: type[StarletteResponse] | type[StarletteStreamingResponse]
        if self._stream is not None:
            response_cls = StarletteStreamingResponse
        else:
            response_cls = StarletteResponse

        if self.req.method == "HEAD" and self._stream is not None:
            body = _empty_async_body()

        response = response_cls(
            body,
            status_code=self.status_code_safe,
            headers=headers,
            background=self._background,
        )
        if self.req.method == "HEAD" and self._stream is None:
            # Preserve the headers Starlette computed from the GET body, but do
            # not send a response body for HEAD.
            response.body = b""
        self._prepare_cookies(response)

        await response(scope, receive, send)

    @property
    def ok(self):
        """``True`` if the status code is in the 2xx range (success).

        Reads as ``200`` until a status code has been set, so it never raises.
        """
        code = self.status_code if self.status_code is not None else 200
        return 200 <= code < 300

    @property
    def status_code_safe(self) -> int:
        """Return the status code, raising ``RuntimeError`` if it hasn't been set."""
        if self.status_code is None:
            raise RuntimeError("HTTP status code has not been defined")
        return self.status_code
