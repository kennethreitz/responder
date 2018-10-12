import io
import json
import gzip

import graphene
import yaml
from requests.models import Request as RequestsRequest
from requests.structures import CaseInsensitiveDict
from werkzeug.wrappers import Request as WerkzeugRequest
from werkzeug.wrappers import BaseResponse as WerkzeugResponse


from urllib.parse import parse_qs

from .status_codes import HTTP_200

# @staticmethod
# def funcname(parameter_list):
#     pass


def flatten(d):
    for key, value in d.copy().items():
        if len(value) == 1:
            d[key] = value[0]

    return d


# TODO: add slots
class Request:
    def __init__(self, environ, start_response=None):
        self._wz = WerkzeugRequest(environ)
        self.start_response = start_response
        self.headers = CaseInsensitiveDict(
            self._wz.headers.to_wsgi_list()
        )  #: A case-insensitive dictionary, containg all headers sent in the Request.
        self.method = (
            self._wz.method.lower()
        )  #: The incoming HTTP method used for the request, lower-cased.
        self.full_url = (
            self._wz.url
        )  #: The full URL of the Request, query parameters and all.
        self.url = (
            self._wz.base_url
        )  #: The URL of the Request, without query parameters.
        self.full_path = (
            self._wz.full_path
        )  #: The full path portion of the URL of the Request, query parameters and all.
        self.path = (
            self._wz.path
        )  #: The path portion of the URL of the Request, without query parameters.
        self.params = flatten(
            parse_qs(self._wz.query_string.decode("utf-8"))
        )  #: A dictionary of the parsed query paramaters used for the Request.
        self.query = self._wz.query_string.decode(
            "utf-8"
        )  #: A string containing only the query paramaters of the Request.
        self.raw = self._wz.stream  #: A raw file-like stream of the incoming Request.
        self.content = self._wz.get_data(
            cache=True, as_text=False
        )  #: The Request body, as bytes.
        self.mimetype = self._wz.mimetype  #: The mimetype of the incoming Request.
        # TODO: rip that out
        self.text = self._wz.get_data(
            cache=False, as_text=True
        )  #: The Request body, as unicode.
        self.formats = None

    @property
    def is_secure(self):
        """Returns ``True`` if the incoming Request was securely made."""
        return self._wz.is_secure

    def accepts(self, content_type):
        """Returns ``True`` if the incoming Request accepts the given ``content_type``."""
        return content_type in self.headers["Accept"]

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
            return (self.content, self.mimetype, {})

        if self.text:
            return (
                self.text.encode(self.encoding),
                self.mimetype or "application/text",
                {"Encoding": self.encoding},
            )

        for format in self.formats:
            if self.req.accepts(format):
                return self.formats[format](self, encode=True), None, {}

        # Default to JSON anyway.
        else:
            return (
                json.dumps(self.media),
                self.mimetype or "application/json",
                {"Content-Type": "application/json"},
            )

    @property
    def gzipped_body(self):

        body, mimetype, headers = self.body

        if isinstance(body, str):
            body = body.encode(self.encoding)

        if "gzip" in self.req.headers["Accept-Encoding"].lower():
            gzip_buffer = io.BytesIO()
            gzip_file = gzip.GzipFile(mode="wb", fileobj=gzip_buffer)
            gzip_file.write(body)
            gzip_file.close()

            new_headers = {
                "Content-Encoding": "gzip",
                # "Vary": "Accept-Encoding",
                "Content-Length": str(len(body)),
            }
            headers.update(new_headers)

            return (gzip_buffer.getvalue(), mimetype, headers)
        else:
            return (body, mimetype, headers)

    @property
    def _wz(self):
        body, mimetype, headers = self.body
        if len(self.body) > 500:
            body, mimetype, headers = self.gzipped_body
        if self.headers:
            headers.update(self.headers)

        r = WerkzeugResponse(
            body,
            status=self.status_code,
            mimetype=self.mimetype or mimetype,
            direct_passthrough=False,
        )
        r.headers = headers
        return r

    def __call__(self, environ, start_response):
        return self._wz(environ, start_response)


class Schema(graphene.Schema):
    def on_request(self, req, resp):
        pass
