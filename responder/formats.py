import json
from urllib.parse import urlencode

import yaml
from requests_toolbelt.multipart import decoder

from .models import QueryDict


async def format_form(r, encode=False):
    if encode:
        pass
    elif "multipart/form-data" in r.headers.get("Content-Type"):
        decode = decoder.MultipartDecoder(await r.content, r.mimetype)
        querys = list()
        for part in decode.parts:
            header = part.headers.get(b"Content-Disposition").decode("utf-8")
            text = part.text

            for section in [h.strip() for h in header.split(";")]:
                split = section.split("=")
                if len(split) > 1:
                    key = split[1]
                    key = key[1:-1]
                    querys.append((key, text))

        content = urlencode(querys)
        return QueryDict(content)
    else:
        return QueryDict(await r.text)


async def format_yaml(r, encode=False):
    if encode:
        r.headers.update({"Content-Type": "application/x-yaml"})
        return yaml.safe_dump(r.media)
    else:
        return yaml.safe_load(await r.content)


async def format_json(r, encode=False):
    if encode:
        r.headers.update({"Content-Type": "application/json"})
        return json.dumps(r.media)
    else:
        return json.loads(await r.content)


async def format_files(r, encode=False):
    if encode:
        pass
    else:
        decoded = decoder.MultipartDecoder(await r.content, r.mimetype)
        dump = {}
        for part in decoded.parts:
            header = part.headers[b"Content-Disposition"].decode("utf-8")
            mimetype = part.headers.get(b"Content-Type", None)
            filename = None

            for section in [h.strip() for h in header.split(";")]:
                split = section.split("=")
                if len(split) > 1:
                    key = split[0]
                    value = split[1]

                    value = value[1:-1]

                    if key == "filename":
                        filename = value
                    elif key == "name":
                        formname = value

            if mimetype is None:
                dump[formname] = part.content
            else:
                dump[formname] = {
                    "filename": filename,
                    "content": part.content,
                    "content-type": mimetype.decode("utf-8"),
                }
        return dump


def get_formats():
    return {
        "json": format_json,
        "yaml": format_yaml,
        "form": format_form,
        "files": format_files,
    }
