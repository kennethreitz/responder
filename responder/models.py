import functools
import inspect
import typing as t
from http.cookies import SimpleCookie
from urllib.parse import parse_qs

import chardet
import rfc3986
from requests.cookies import RequestsCookieJar
from requests.structures import CaseInsensitiveDict
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


class QueryDict(dict):
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
        return self.headers.get("Content-Type", "")

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
        return rfc3986.urlparse(self.full_url)

    @property
    def cookies(self):
        """The cookies sent in the Request, as a dictionary."""
        if self._cookies is None:
            cookies = RequestsCookieJar()
            cookie_header = self.headers.get("Cookie", "")

            bc: SimpleCookie = SimpleCookie(cookie_header)
            for key, morsel in bc.items():
                cookies[key] = morsel.value

            self._cookies = cookies.get_dict()

        return self._cookies

    @property
    def params(self):
        """A dictionary of the parsed query parameters used for the Request."""
        try:
            return QueryDict(self.url.query)
        except AttributeError:
            return QueryDict({})

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
        """The apparent encoding, provided by the chardet library. Must be awaited."""
        declared_encoding = await self.declared_encoding

        if declared_encoding:
            return declared_encoding

        return chardet.detect(await self.content)["encoding"] or DEFAULT_ENCODING

    @property
    def is_secure(self):
        return self.url.scheme == "https"

    def accepts(self, content_type):
        """Returns ``True`` if the incoming Request accepts the given ``content_type``."""
        return content_type in self.headers.get("Accept", [])

    async def media(self, format: t.Union[str, t.Callable] = None):  # noqa: A001, A002
        """Renders incoming json/yaml/form data as Python objects. Must be awaited.

        :param format: The name of the format being used.
                       Alternatively, accepts a custom callable for the format type.
        """

        if format is None:
            format = "yaml" if "yaml" in self.mimetype or "" else "json"  # noqa: A001
            format = "form" if "form" in self.mimetype or "" else format  # noqa: A001

        formatter: t.Callable
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
        #: The HTTP Status Code to use for the Response.
        self.status_code: t.Union[int, None] = None
        self.content = None  #: A bytes representation of the response body.
        self.mimetype = None
        self.encoding = DEFAULT_ENCODING
        self.media = None  #: A Python object that will be content-negotiated and
        #: sent back to the client. Typically, in JSON formatting.
        self._stream = None
        self.headers = {}  #: A Python dictionary of ``{key: value}``,
        #: representing the headers of the response.
        self.formats = formats
        self.cookies: SimpleCookie = SimpleCookie()  #: The cookies set in the Response
        self.session = (
            req.session
        )  #: The cookie-based session data, in dict form, to add to the Response.

    # Property or func/dec
    def stream(self, func, *args, **kwargs):
        assert inspect.isasyncgenfunction(func)

        self._stream = functools.partial(func, *args, **kwargs)

        return func

    def redirect(self, location, *, set_text=True, status_code=HTTP_301):
        self.status_code = status_code
        if set_text:
            self.text = f"Redirecting to: {location}"
        self.headers.update({"Location": location})

    @property
    async def body(self):
        if self._stream is not None:
            return (self._stream(), {})

        if self.content is not None:
            headers = {}
            content = self.content
            if self.mimetype is not None:
                headers["Content-Type"] = self.mimetype
            if self.mimetype == "text/plain" and self.encoding is not None:
                headers["Encoding"] = self.encoding
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

        response_cls: t.Union[
            t.Type[StarletteResponse], t.Type[StarletteStreamingResponse]
        ]
        if self._stream is not None:
            response_cls = StarletteStreamingResponse
        else:
            response_cls = StarletteResponse

        response = response_cls(body, status_code=self.status_code_safe, headers=headers)
        self._prepare_cookies(response)

        await response(scope, receive, send)

    @property
    def ok(self):
        return 200 <= self.status_code_safe < 300

    @property
    def status_code_safe(self) -> int:
        if self.status_code is None:
            raise RuntimeError("HTTP status code has not been defined")
        return self.status_code
