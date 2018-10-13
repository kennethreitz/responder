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
    def __init__(self, query_string):
        query_string = query_string
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
    def __init__(self, scope, receive):
        self._starlette = StarletteRequest(scope, receive)
        self.formats = None

        headers = CaseInsensitiveDict()
        for header, value in self._starlette.headers.items():
            headers[header] = value

        self.headers = (
            headers
        )  #: A case-insensitive dictionary, containg all headers sent in the Request.

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
            )  #: A dictionary of the parsed query paramaters used for the Request.
        except AttributeError:
            self.params = {}

    @property
    async def content(self):
        """The Request body, as bytes."""
        return (await self._starlette.body()).encode(self.encoding)

    @property
    async def text(self):
        """The Request body, as unicode."""
        return await self._starlette.body()

    @property
    def is_secure(self):
        return self.url.scheme == "https"

    def accepts(self, content_type):
        """Returns ``True`` if the incoming Request accepts the given ``content_type``."""
        return content_type in self.headers["Accept"]

    def media(self, format=None):
        """Renders incoming json/yaml/form data as Python objects.

        :param format: The name of the format being used. Alternatively accepts a custom callable for the format type.
        """
        print(repr(format))

        if format is None:
            format = "yaml" if "yaml" in self.mimetype or "" else "json"

        if format in self.formats:
            return self.formats[format](self)
        else:
            return format(self)


class Response:
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
            return (json.dumps(self.media), {"Content-Type": "application/json"})

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
