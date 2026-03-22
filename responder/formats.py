from __future__ import annotations

import json
from urllib.parse import urlencode

import yaml
from python_multipart import MultipartParser

from .models import QueryDict


class _PartData:
    __slots__ = ("headers", "body", "header_field")

    def __init__(self):
        self.headers: dict[str, str] = {}
        self.body = b""
        self.header_field = ""


def _parse_multipart(content: bytes, content_type: str) -> list[_PartData]:
    """Parse multipart form data into a list of parts with headers and body."""
    boundary = None
    for segment in content_type.split(";"):
        segment = segment.strip()
        if segment.startswith("boundary="):
            boundary = segment.split("=", 1)[1].strip('"')
            break

    if boundary is None:
        return []

    parts: list[_PartData] = []
    current: list[_PartData | None] = [None]

    def on_part_begin():
        current[0] = _PartData()

    def on_part_data(data, start, end):
        current[0].body += data[start:end]  # type: ignore[union-attr]

    def on_header_field(data, start, end):
        current[0].header_field = data[start:end].decode("utf-8")  # type: ignore[union-attr]

    def on_header_value(data, start, end):
        part = current[0]
        assert part is not None
        part.headers[part.header_field] = data[start:end].decode("utf-8")

    def on_part_end():
        parts.append(current[0])  # type: ignore[arg-type]

    parser = MultipartParser(
        boundary.encode(),
        {  # type: ignore[arg-type]
            "on_part_begin": on_part_begin,
            "on_part_data": on_part_data,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_part_end": on_part_end,
        },
    )
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
