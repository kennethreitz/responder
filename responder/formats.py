from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import warnings
from decimal import Decimal
from urllib.parse import urlencode
from uuid import UUID

import yaml
from python_multipart import MultipartParser
from starlette.exceptions import HTTPException

from .models import QueryDict


def _json_default(obj):
    """``json.dumps``/``msgpack`` fallback for common non-JSON-native types.

    Handles Pydantic models, dataclasses, ``datetime``/``date``/``time``,
    ``UUID``, ``Decimal``, ``set``/``frozenset``, and ``bytes`` so that
    ``resp.media = {"created_at": datetime.now()}`` (or a model) just works.
    """
    if hasattr(obj, "model_dump"):  # pydantic BaseModel
        return obj.model_dump(mode="json")
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _jsonable(obj, default=_json_default):
    """Recursively convert ``obj`` to JSON/YAML-native types.

    Used by encoders (YAML) that have no ``default=`` hook. ``default`` handles
    any leaf type not covered here (and may be a user-supplied ``encoder``).
    """
    if obj is None or isinstance(obj, (str, bool, int, float)):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _jsonable(dataclasses.asdict(obj), default)
    if isinstance(obj, dict):
        return {k: _jsonable(v, default) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_jsonable(v, default) for v in obj]
    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    # Defer to the (possibly user-supplied) hook, then normalize its output.
    return _jsonable(default(obj), default)


def _make_default_hook(encoder):
    """Compose a user ``encoder`` with the built-in type fallback.

    The user's ``encoder`` is tried first; if it doesn't handle the object
    (raises ``TypeError``/``NotImplementedError``), the built-in conversions
    apply. ``None`` means "just the built-ins".
    """
    if encoder is None:
        return _json_default

    def hook(obj):
        try:
            return encoder(obj)
        except (TypeError, NotImplementedError):
            return _json_default(obj)

    return hook


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
    if "multipart/form-data" in r.mimetype:
        parts = _parse_multipart(await r.content, r.mimetype)
        queries = []
        for part in parts:
            header = part.headers.get("Content-Disposition", "")
            try:
                text = part.body.decode("utf-8")
            except UnicodeDecodeError:
                # A binary part (a file) — not a text form field; use
                # req.media("files") for those.
                continue

            for section in [h.strip() for h in header.split(";")]:
                split = section.split("=")
                if len(split) > 1:
                    key = split[1]
                    key = key[1:-1]
                    queries.append((key, text))

        content = urlencode(queries)
        return QueryDict(content)
    return QueryDict(await r.text)


def _make_yaml_format(hook):
    async def format_yaml(r, encode=False):
        if encode:
            r.headers.update({"Content-Type": "application/x-yaml"})
            return yaml.safe_dump(_jsonable(r.media, hook))
        try:
            return yaml.safe_load(await r.content)
        except yaml.YAMLError as exc:
            raise HTTPException(status_code=400, detail="Invalid YAML body") from exc

    return format_yaml


def _make_json_format(hook, ensure_ascii=True):
    async def format_json(r, encode=False):
        if encode:
            r.headers.update({"Content-Type": "application/json"})
            return json.dumps(r.media, default=hook, ensure_ascii=ensure_ascii)
        try:
            return json.loads(await r.content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    return format_json


async def format_files(r, encode=False):
    if encode:
        return None
    warnings.warn(
        "req.media('files') returns a fully-buffered bytes-dict; in Responder "
        "6.0 it will return streaming UploadFile objects. Use File() markers "
        "for the new API (def handler(req, resp, *, f: UploadFile = File(...))).",
        DeprecationWarning,
        stacklevel=2,
    )
    parts = _parse_multipart(await r.content, r.mimetype)
    dump: dict[str, object] = {}
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


def _make_msgpack_format(hook):
    async def format_msgpack(r, encode=False):
        try:
            import msgpack
        except ImportError as exc:
            raise ImportError(
                "msgpack is required for MessagePack support: pip install msgpack"
            ) from exc

        if encode:
            r.headers.update({"Content-Type": "application/x-msgpack"})
            return msgpack.packb(r.media, default=hook)
        try:
            return msgpack.unpackb(await r.content)
        except (ValueError, msgpack.exceptions.UnpackException) as exc:
            raise HTTPException(
                status_code=400, detail="Invalid MessagePack body"
            ) from exc

    return format_msgpack


def get_formats(encoder=None, json_ensure_ascii=True):
    """Return the content-negotiation formatters.

    :param encoder: Optional ``obj -> serializable`` callable applied across
        **all** response formats (JSON, YAML, MessagePack) to convert otherwise
        unserializable objects. It is tried first and falls back to the built-in
        conversions (datetime/date/time/UUID/Decimal/set/dataclass/Pydantic
        model). ``None`` uses only the built-ins.
    :param json_ensure_ascii: If ``True`` (the default in 5.x), JSON escapes
        non-ASCII as ``\\uXXXX``; ``False`` emits raw UTF-8. The default flips to
        ``False`` in Responder 6.0.
    """
    hook = _make_default_hook(encoder)
    return {
        "json": _make_json_format(hook, ensure_ascii=json_ensure_ascii),
        "yaml": _make_yaml_format(hook),
        "form": format_form,
        "files": format_files,
        "msgpack": _make_msgpack_format(hook),
    }
