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


class Request:
    def __init__(self):
        super().__init__()
        self._wz = None

    @classmethod
    def from_environ(kls, environ, start_response=None):
        self = kls()
        self._wz = WerkzeugRequest(environ)
        self.headers = CaseInsensitiveDict(self._wz.headers.to_list())
        self.method = self._wz.method.lower()
        self.full_url = self._wz.url
        self.url = self._wz.base_url
        self.full_path = self._wz.full_path
        self.path = self._wz.path
        self.params = parse_qs(self._wz.query_string.decode("utf-8"))
        self.query = self._wz.query_string.decode("utf-8")
        self.raw = self._wz.stream
        self.content = self._wz.get_data(cache=True, as_text=False)
        self.mimetype = self._wz.mimetype
        self.accepts_mimetypes = self._wz.accept_mimetypes
        self.text = self._wz.get_data(cache=True, as_text=True)
        self.data = self._wz.get_data(cache=True, as_text=True, parse_form_data=True)
        self.dispatched = False
        self._start_response = start_response
        self._environ = environ

        return self

    @property
    def accepts_yaml(self):
        return "yaml" in self.headers["accept"]

    @property
    def accepts_json(self):
        return "json" in self.headers["accept"]

    def json(self):
        return json.loads(self.content)

    def yaml(self):
        return yaml.load(self.content)


class Response:
    def __init__(self, req):
        self.req = req
        self.status_code = HTTP_200
        self.text = None
        self.content = None
        self.encoding = "utf-8"
        self.media = None
        self.mimetype = None
        self.headers = {}

    @property
    def body(self):
        print(self.__dict__)
        if self.content:
            return (self.content, self.mimetype, {})

        if self.text:
            return (
                self.text.encode(self.encoding),
                self.mimetype or "application/text",
                {"Encoding": self.encoding},
            )

        if self.req.accepts_yaml:
            return (
                yaml.dump(self.media).encode(self.encoding),
                self.mimetype or "application/x-yaml",
                {"Content-Type": "application/x-yaml"},
            )
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
        # print(self.req.headers)
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
