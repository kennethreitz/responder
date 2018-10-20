from urllib.parse import parse_qs

import yaml
import json
from parse import findall
from .models import QueryDict
from requests_toolbelt.multipart import decoder


async def format_form(r, encode=False):
    if encode:
        pass
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
        decoded = decoder.MultipartDecoder(await r.content, r.headers["Content-Type"])
        dump = {}
        for part in decoded.parts:
            header = part.headers[b"Content-Disposition"].decode("utf-8")
            filename = None

            for section in [h.strip() for h in header.split(";")]:
                split = section.split("=")
                if len(split) > 1:
                    key = split[0]
                    value = split[1]

                    value = value[1:-1]

                    if key == "filename":
                        filename = value

            if filename:
                dump[filename] = part.content
        return dump


def get_formats():
    return {
        "json": format_json,
        "yaml": format_yaml,
        "form": format_form,
        "files": format_files,
    }
