import io
import json
import gzip

import rfc3986
import graphene
import yaml
from requests.structures import CaseInsensitiveDict
from starlette.datastructures import MutableHeaders
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse


from urllib.parse import parse_qs

from .status_codes import HTTP_200


class QueryDict(dict):
    def __init__(self, query_string=None):
        if query_string is not None:
            dict.update(self, parse_qs(query_string))

    @classmethod
    def fromdict(cls, d):
        instance = cls()
        dict.update(instance, d)
        return instance

    @classmethod
    def fromkeys(cls, seq, value=''):
        """
        Create a new QueryDict with keys from seq and values set to value.
        """
        q = cls('')
        for key in seq:
            q.setlistdefault(key).append(value)
        return q

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
        super().__setitem__(key, [value])

    def __copy__(self):
        return self.fromdict(self)

    def __deepcopy__(self, memo):
        result = self.__class__()
        memo[id(self)] = result
        for key, value in dict.items(self):
            dict.__setitem__(result, copy.deepcopy(key, memo),
                copy.deepcopy(value, memo))
        return result

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

    def _getlist(self, key, default=None, force_list=False):
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

    def getlist(self, key, default=None):
        """
        Return the list of values for the key. If key doesn't exist, return a
        default value.
        """
        return self._getlist(key, default, force_list=True)

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

    def values(self):
        """Yield the last value on every key list."""
        for key in self:
            yield self[key]

    def _setlist(self, key, list_):
        super().__setitem__(key, list_)

    def setlist(self, key, list_):
        self._setlist(key, list_[:])

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
            # Do not return default here because __setitem__() may store
            # another value -- QueryDict.__setitem__() does. Look it up.
        return self[key]

    def setlistdefault(self, key, default_list=None):
        if key not in self:
            if default_list is None:
                default_list = []
            self._setlist(key, default_list)
            # Do not return default_list here because _setlist() store
            # another value -- QueryDict.setlist() does. Look it up.
        return self._getlist(key)

    def update(self, *args, **kwargs):
        """Extend rather than replace existing key lists."""
        if len(args) > 1:
            raise TypeError(f"update expected at most 1 argument, got {len(args)}")
        if args:
            dict_ = args[0]
            if isinstance(dict_, QueryDict):
                for key, value in dict_.items_list():
                    self.setlistdefault(key).extend(value)
            else:
                try:
                    for key, value in dict_.items():
                        self.setlistdefault(key).append(value)
                except:
                    raise ValueError("QueryDict.update() takes either a QueryDict or dictionary")
        for key, value in kwargs.items():
            self.setlistdefault(key).append(value)


# TODO: add slots
class Request:
    __slots__ = [
        "_starlette",
        "encoding",
        "formats",
        "headers",
        "mimetype",
        "method",
        "full_url",
        "url",
        "params",
    ]

    def __init__(self, scope, receive):
        self._starlette = StarletteRequest(scope, receive)
        self.formats = None
        self.encoding = "utf-8"

        headers = CaseInsensitiveDict()
        for header, value in self._starlette.headers.items():
            headers[header] = value

        self.headers = (
            headers
        )  #: A case-insensitive dictionary, containing all headers sent in the Request.

        self.mimetype = self.headers.get("Content-Type", "")

        self.method = (
            self._starlette.method.lower()
        )  #: The incoming HTTP method used for the request, lower-cased.

        self.full_url = str(
            self._starlette.url
        )  #: The full URL of the Request, query parameters and all.

        self.url = rfc3986.urlparse(self.full_url)  #: The parsed URL of the Request
        try:
            self.params = QueryDict(
                self.url.query
            )  #: A dictionary of the parsed query parameters used for the Request.
        except AttributeError:
            self.params = {}

    @property
    async def content(self):
        """The Request body, as bytes."""
        return await self._starlette.body()

    @property
    async def text(self):
        """The Request body, as unicode."""
        return (await self._starlette.body()).decode(self.encoding)

    @property
    def is_secure(self):
        return self.url.scheme == "https"

    def accepts(self, content_type):
        """Returns ``True`` if the incoming Request accepts the given ``content_type``."""
        return content_type in self.headers.get("Accept", [])

    def media(self, format=None):
        """Renders incoming json/yaml/form data as Python objects.

        :param format: The name of the format being used. Alternatively accepts a custom callable for the format type.
        """

        if format is None:
            format = "yaml" if "yaml" in self.mimetype or "" else "json"

        if format in self.formats:
            return self.formats[format](self)
        else:
            return format(self)


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
    ]

    def __init__(self, req, *, formats):
        self.req = req
        self.status_code = HTTP_200  #: The HTTP Status Code to use for the Response.
        self.text = None  #: A unicode representation of the response body.
        self.content = None  #: A bytes representation of the response body.
        self.encoding = "utf-8"
        self.media = (
            None
        )  #: A Python object that will be content-negotiated and sent back to the client. Typically, in JSON formatting.
        self.headers = (
            {}
        )  #: A Python dictionary of {Key: value}, representing the headers of the response.
        self.formats = formats

    @property
    def body(self):
        if self.content:
            return (self.content, {})

        if self.text:
            return (self.text.encode(self.encoding), {"Encoding": self.encoding})

        for format in self.formats:
            if self.req.accepts(format):
                return self.formats[format](self, encode=True), {}

        # Default to JSON anyway.
        else:
            return (
                self.formats["json"](self, encode=True),
                {"Content-Type": "application/json"},
            )

    @property
    def gzipped_body(self):

        body, headers = self.body

        if isinstance(body, str):
            body = body.encode(self.encoding)

        if "gzip" in self.req.headers["Accept-Encoding"].lower():
            gzip_buffer = io.BytesIO()
            gzip_file = gzip.GzipFile(mode="wb", fileobj=gzip_buffer)
            gzip_file.write(body)
            gzip_file.close()

            new_headers = {
                "Content-Encoding": "gzip",
                "Vary": "Accept-Encoding",
                "Content-Length": str(len(body)),
            }
            headers.update(new_headers)

            return (gzip_buffer.getvalue(), headers)
        else:
            return (body, headers)

    async def __call__(self, receive, send):
        body, headers = self.body
        if len(self.body) > 500:
            body, headers = self.gzipped_body
        if self.headers:
            headers.update(self.headers)

        response = StarletteResponse(
            body, status_code=self.status_code, headers=headers
        )
        await response(receive, send)


class Schema(graphene.Schema):
    def on_request(self, req, resp):
        pass
