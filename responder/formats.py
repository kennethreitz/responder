import json
from urllib.parse import urlencode

import yaml
from multipart import MultipartParser

from .models import QueryDict


def _parse_multipart(content, content_type):
    """Parse multipart form data and return list of (headers_dict, body_bytes) tuples."""
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part.split("=", 1)[1].strip('"')
            break

    if boundary is None:
        return []

    parts = []
    parser_parts = []

    class PartData:
        def __init__(self):
            self.headers = {}
            self.body = b""

    current = [None]

    def on_part_begin():
        current[0] = PartData()

    def on_part_data(data, start, end):
        current[0].body += data[start:end]

    def on_header_value(data, start, end):
        current[0]._last_header_value = data[start:end].decode("utf-8")

    def on_header_field(data, start, end):
        current[0]._last_header_field = data[start:end].decode("utf-8")

    def on_header_end():
        field = current[0]._last_header_field
        value = current[0]._last_header_value
        current[0].headers[field] = value

    def on_part_end():
        parts.append(current[0])

    callbacks = {
        "on_part_begin": on_part_begin,
        "on_part_data": on_part_data,
        "on_header_field": on_header_field,
        "on_header_value": on_header_value,
        "on_headers_finished": on_header_end,
        "on_part_end": on_part_end,
    }

    parser = MultipartParser(boundary.encode(), callbacks)
    parser.write(content)
    parser.finalize()

    return parts


async def format_form(r, encode=False):
    if encode:
        return None
    if "multipart/form-data" in r.headers.get("Content-Type"):
        parts = _parse_multipart(await r.content, r.mimetype)
        queries = []
        for part in parts:
            header = part.headers.get("Content-Disposition", "")
            text = part.body.decode("utf-8")

            for section in [h.strip() for h in header.split(";")]:
                split = section.split("=")
                if len(split) > 1:
                    key = split[1]
                    key = key[1:-1]
                    queries.append((key, text))

        content = urlencode(queries)
        return QueryDict(content)
    return QueryDict(await r.text)


async def format_yaml(r, encode=False):
    if encode:
        r.headers.update({"Content-Type": "application/x-yaml"})
        return yaml.safe_dump(r.media)
    return yaml.safe_load(await r.content)


async def format_json(r, encode=False):
    if encode:
        r.headers.update({"Content-Type": "application/json"})
        return json.dumps(r.media)
    return json.loads(await r.content)


async def format_files(r, encode=False):
    if encode:
        return None
    parts = _parse_multipart(await r.content, r.mimetype)
    dump = {}
    for part in parts:
        header = part.headers.get("Content-Disposition", "")
        mimetype = part.headers.get("Content-Type", None)
        filename = None
        formname = None

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

        if formname is None:
            continue

        if mimetype is None:
            dump[formname] = part.body
        else:
            dump[formname] = {
                "filename": filename,
                "content": part.body,
                "content-type": mimetype,
            }
    return dump


def get_formats():
    return {
        "json": format_json,
        "yaml": format_yaml,
        "form": format_form,
        "files": format_files,
    }
