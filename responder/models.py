import io
import json
import gzip
from http.cookies import SimpleCookie


import chardet
import rfc3986
import graphene
import yaml
from requests.structures import CaseInsensitiveDict
from requests.cookies import RequestsCookieJar
from starlette.datastructures import MutableHeaders
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

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


# TODO: add slots
class Request:
    __slots__ = ["_starlette", "formats", "_headers", "_encoding", "api"]

    def __init__(self, scope, receive, api=None):
        self._starlette = StarletteRequest(scope, receive)
        self.formats = None
        self._encoding = None
        self.api = api

        headers = CaseInsensitiveDict()
        for header, value in self._starlette.headers.items():
            headers[header] = value

        self._headers = headers

    @property
    def session(self):
        """The session data, in dict form, from the Request."""
        if "Responder-Session" in self.cookies:
            data = self.cookies["Responder-Session"]
            data = self.api._signer.unsign(data)
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
        cookies = RequestsCookieJar()
        cookie_header = self.headers.get("cookie", "")

        # if cookie_header:
        bc = SimpleCookie(cookie_header)
        for k, v in bc.items():
            cookies[k] = v

        return cookies.get_dict()

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

        # Then try what's defined by the Request.
        elif await self.declared_encoding:
            return self.declared_encoding

        # Then, automatically detect the encoding.
        else:
            return await self.apparent_encoding

    @encoding.setter
    def encoding(self, value):
        self._encoding = value

    @property
    async def content(self):
        """The Request body, as bytes. Must be awaited."""
        return await self._starlette.body()

    @property
    async def text(self):
        """The Request body, as unicode. Must be awaited."""
        return (await self._starlette.body()).decode(await self.encoding)

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
        else:
            return chardet.detect(await self.content)["encoding"]

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


class Response:
    __slots__ = [
        "req",
        "status_code",
        "text",
        "content",
        "encoding",
        "media",
        "headers",
        "formats",
        "cookies",
        "session",
    ]

    def __init__(self, req, *, formats):
        self.req = req
        self.status_code = HTTP_200  #: The HTTP Status Code to use for the Response.
        self.text = None  #: A unicode representation of the response body.
        self.content = None  #: A bytes representation of the response body.
        self.encoding = DEFAULT_ENCODING
        self.media = (
            None
        )  #: A Python object that will be content-negotiated and sent back to the client. Typically, in JSON formatting.
        self.headers = (
            {}
        )  #: A Python dictionary of {Key: value}, representing the headers of the response.
        self.formats = formats
        self.cookies = {}  #: The cookies set in the Response, as a dictionary
        self.session = req.session.copy()  #: """The *cookie-based* session data, in dict form, to add to the Response."""

    @property
    async def body(self):
        if self.content:
            return (self.content, {})

        if self.text:
            return (self.text.encode(self.encoding), {"Encoding": self.encoding})

        for format in self.formats:
            if self.req.accepts(format):
                return (await self.formats[format](self, encode=True)), {}

        # Default to JSON anyway.
        return (
            await self.formats["json"](self, encode=True),
            {"Content-Type": "application/json"},
        )

    async def __call__(self, receive, send):
        body, headers = await self.body
        if self.headers:
            headers.update(self.headers)

        response = StarletteResponse(
            body, status_code=self.status_code, headers=headers
        )
        await response(receive, send)
