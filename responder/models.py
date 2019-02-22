import functools
import io
import inspect
import json
import gzip
from base64 import b64decode
from http.cookies import SimpleCookie


import chardet
import rfc3986
import graphene
import yaml
from requests.structures import CaseInsensitiveDict
from requests.cookies import RequestsCookieJar
from starlette.datastructures import MutableHeaders
from starlette.requests import Request as StarletteRequest
from starlette.responses import (
    Response as StarletteResponse,
    StreamingResponse as StarletteStreamingResponse,
)

from urllib.parse import parse_qs

from .status_codes import HTTP_200
from .statics import DEFAULT_ENCODING


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

    def __init__(self, scope, receive, api=None):
        self._starlette = StarletteRequest(scope, receive)
        self.formats = None
        self._encoding = None
        self.api = api
        self._content = None

        headers = CaseInsensitiveDict()
        for key, value in self._starlette.headers.items():
            headers[key] = value

        self._headers = headers
        self._cookies = None

    @property
    def session(self):
        """The session data, in dict form, from the Request."""
        if self.api.session_cookie in self.cookies:

            data = self.cookies[self.api.session_cookie]

            data = self.api._signer.unsign(data)
            data = b64decode(data)
            return json.loads(data)
        return {}

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

            bc = SimpleCookie(cookie_header)
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

    async def media(self, format=None):
        """Renders incoming json/yaml/form data as Python objects. Must be awaited.

        :param format: The name of the format being used. Alternatively accepts a custom callable for the format type.
        """

        if format is None:
            format = "yaml" if "yaml" in self.mimetype or "" else "json"
            format = "form" if "form" in self.mimetype or "" else format

        if format in self.formats:
            return await self.formats[format](self)
        else:
            return await format(self)


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
        self.status_code = None  #: The HTTP Status Code to use for the Response.
        self.content = None  #: A bytes representation of the response body.
        self.mimetype = None
        self.encoding = DEFAULT_ENCODING
        self.media = (
            None
        )  #: A Python object that will be content-negotiated and sent back to the client. Typically, in JSON formatting.
        self._stream = None
        self.headers = (
            {}
        )  #: A Python dictionary of ``{key: value}``, representing the headers of the response.
        self.formats = formats
        self.cookies = SimpleCookie()  #: The cookies set in the Response
        self.session = (
            req.session.copy()
        )  #: The cookie-based session data, in dict form, to add to the Response.

    # Property or func/dec
    def stream(self, func, *args, **kwargs):
        assert inspect.isasyncgenfunction(func)

        self._stream = functools.partial(func, *args, **kwargs)

        return func

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

        for format in self.formats:
            if self.req.accepts(format):
                return (await self.formats[format](self, encode=True)), {}

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

    async def __call__(self, receive, send):
        body, headers = await self.body
        if self.headers:
            headers.update(self.headers)

        if self._stream is not None:
            response_cls = StarletteStreamingResponse
        else:
            response_cls = StarletteResponse

        response = response_cls(body, status_code=self.status_code, headers=headers)
        self._prepare_cookies(response)

        await response(receive, send)
