import io
import json
import gzip

import graphene
import yaml
from starlette.datastructures import MutableHeaders
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse


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
class Request(StarletteRequest):
    def __init__(self, scope, receive):
        super().__init__(scope, receive=receive)

    @property
    def is_secure(self):
        return self.url.scheme == 'https'

    def accepts(self, content_type):
        return content_type in self.headers["Accept"]


class Response:
    def __init__(self, req, formats):
        self.req = req
        self.status_code = HTTP_200
        self.formats = formats
        self.text = None
        self.content = None
        self.encoding = "utf-8"
        self.media = None
        self.mimetype = None
        self.headers = MutableHeaders()

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

    async def __call__(self, receive, send):
        body, mimetype, headers = self.body
        if len(self.body) > 500:
            body, mimetype, headers = self.gzipped_body
        if self.headers:
            headers.update(self.headers)

        response = StarletteResponse(
            body,
            status_code=self.status_code,
            headers=headers,
            media_type=self.mimetype or mimetype,
        )
        await response(receive, send)


class Schema(graphene.Schema):
    def on_request(self, req, resp):
        pass
