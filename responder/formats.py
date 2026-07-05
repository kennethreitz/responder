from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import warnings
from decimal import Decimal
from email.message import Message
from email.utils import collapse_rfc2231_value
from urllib.parse import urlencode
from uuid import UUID

import yaml
from python_multipart import MultipartParser
from starlette.exceptions import HTTPException

from .models import QueryDict

try:  # Optional fast JSON backend: pip install "responder[orjson]"
    import orjson as _orjson
except ImportError:  # pragma: no cover
    # The [assignment] code only fires when orjson is installed (assigning None
    # to a module-typed name); [unused-ignore] keeps the comment valid in envs
    # without orjson, where the import is unresolved and the ignore is moot.
    _orjson = None  # type: ignore[assignment, unused-ignore]

if _orjson is not None:
    # Passthrough options keep orjson output-compatible with the stdlib path:
    # datetimes, dataclasses, and builtin subclasses are routed through the
    # (possibly user-supplied) ``default=`` hook instead of orjson's native
    # serializers, and non-string dict keys are coerced to strings the way
    # ``json.dumps`` does.
    _ORJSON_OPTIONS = (
        _orjson.OPT_NON_STR_KEYS
        | _orjson.OPT_PASSTHROUGH_DATACLASS
        | _orjson.OPT_PASSTHROUGH_DATETIME
        | _orjson.OPT_PASSTHROUGH_SUBCLASS
    )


def _make_orjson_default(hook):
    """Adapt the composed ``default=`` hook for orjson.

    ``OPT_PASSTHROUGH_SUBCLASS`` routes subclasses of ``str``/``int``/
    ``dict``/``list`` here; convert them through their overridden accessors,
    matching the stdlib encoder (e.g. a ``QueryDict`` collapses each key to
    its last value via ``items()``, not its raw list storage).
    """

    def orjson_default(obj):
        if isinstance(obj, dict):
            return dict(obj.items())
        if isinstance(obj, (list, tuple)):
            return list(obj)
        if isinstance(obj, str):
            # Emit the *base* string, matching the stdlib encoder. Calling
            # ``str(obj)`` would route through an overridden ``__str__`` and
            # diverge (e.g. an ``Enum`` str-subclass with a custom repr).
            return str.__str__(obj)
        if isinstance(obj, bool):  # bool before int: it is an int subclass
            return bool(obj)
        if isinstance(obj, int):
            # Emit the *base* integer, matching the stdlib encoder. ``int(obj)``
            # would honour an overridden ``__int__``/``__index__`` and diverge.
            return int.__index__(obj)
        return hook(obj)

    return orjson_default


# Once-per-process latch for the Decimal-to-float legacy path below.
_decimal_float_warned = False


def _warn_decimal_to_float():
    """Warn (once per process) that Decimal-to-float serialization is lossy."""
    global _decimal_float_warned
    if _decimal_float_warned:
        return
    _decimal_float_warned = True
    warnings.warn(
        "Serializing decimal.Decimal to JSON as a float is lossy and "
        "deprecated; Decimal values serialize as strings by default. Convert "
        "explicitly (str(value) or float(value)) before assigning to "
        "resp.media, or pass API(json_decimal='float') to keep the legacy "
        "representation while migrating.",
        DeprecationWarning,
        stacklevel=2,
    )


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
        return str(obj)
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _json_default_float_decimal(obj):
    """Legacy fallback that serializes ``Decimal`` values as JSON floats."""
    if isinstance(obj, Decimal):
        _warn_decimal_to_float()
        return float(obj)
    return _json_default(obj)


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
        return default(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    # Defer to the (possibly user-supplied) hook, then normalize its output.
    return _jsonable(default(obj), default)


def _make_default_hook(encoder, json_decimal="string"):
    """Compose a user ``encoder`` with the built-in type fallback.

    The user's ``encoder`` is tried first; if it doesn't handle the object
    (raises ``TypeError``/``NotImplementedError``), the built-in conversions
    apply. ``None`` means "just the built-ins".
    """
    if json_decimal == "string":
        fallback = _json_default
    elif json_decimal == "float":
        fallback = _json_default_float_decimal
    else:
        raise ValueError("json_decimal= must be 'float' or 'string'")

    if encoder is None:
        return fallback

    def hook(obj):
        try:
            return encoder(obj)
        except (TypeError, NotImplementedError):
            return fallback(obj)

    return hook


class _PartData:
    __slots__ = ("headers", "body", "header_field")

    def __init__(self):
        self.headers: dict[str, str] = {}
        # Accumulated as a bytearray while parsing (the parser may deliver a
        # part's body in many chunks; ``bytes +=`` would be quadratic), then
        # converted to ``bytes`` at part end.
        self.body: bytes | bytearray = bytearray()
        self.header_field = ""


def _parse_multipart(content: bytes, content_type: str) -> list[_PartData]:
    """Parse multipart form data into a list of parts with headers and body."""
    boundary = None
    for segment in content_type.split(";"):
        segment = segment.strip()
        # Parameter names are case-insensitive; the boundary value is not.
        if segment.lower().startswith("boundary="):
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
        part = current[0]
        assert part is not None
        part.body = bytes(part.body)
        parts.append(part)

    parser = MultipartParser(
        boundary.encode(),
        {
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


def _content_disposition_param(header: str, param: str) -> str | None:
    """Extract a single Content-Disposition parameter (e.g. ``name``), using a
    real header parser so quoting and RFC 2231 encoding are handled correctly."""
    message = Message()
    message["content-disposition"] = header
    value = message.get_param(param, header="content-disposition")
    if value is None:
        return None
    # RFC 2231 extended values come back as a (charset, lang, value) tuple.
    if isinstance(value, tuple):
        return collapse_rfc2231_value(value)
    return value


async def format_form(r, encode=False):
    if encode:
        return None
    # Media types are case-insensitive (RFC 7231 §3.1.1.1).
    if "multipart/form-data" in r.mimetype.lower():
        parts = _parse_multipart(await r.content, r.mimetype)
        queries = []
        for part in parts:
            header = part.headers.get("Content-Disposition", "")
            if not header:
                continue
            # A part with a filename is a file, not a text field — read those
            # via req.media("files"). Skip it (this also stops a file's name
            # from leaking in as a phantom form field).
            if _content_disposition_param(header, "filename") is not None:
                continue
            name = _content_disposition_param(header, "name")
            if name is None:
                continue
            try:
                text = part.body.decode("utf-8")
            except UnicodeDecodeError:
                continue
            queries.append((name, text))

        return QueryDict(urlencode(queries))
    return QueryDict(await r.text)


def _make_yaml_format(hook):
    async def format_yaml(r, encode=False):
        if encode:
            # RFC 9512 registered media type (was ``application/x-yaml``).
            r.headers.setdefault("Content-Type", "application/yaml")
            return yaml.safe_dump(_jsonable(r.media, hook))
        content = await r.content
        # An empty body is a 400, matching the JSON format (``safe_load``
        # would otherwise silently return ``None``).
        if not content.strip():
            raise HTTPException(status_code=400, detail="Invalid YAML body")
        try:
            return yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise HTTPException(status_code=400, detail="Invalid YAML body") from exc

    return format_yaml


def _make_json_format(hook, ensure_ascii=True):
    # orjson is UTF-8-only, so the legacy ``json_ensure_ascii=True`` path
    # (escaping non-ASCII as ``\uXXXX``) always stays on the stdlib encoder.
    use_orjson = _orjson is not None and not ensure_ascii
    orjson_hook = _make_orjson_default(hook) if use_orjson else None

    async def format_json(r, encode=False):
        if encode:
            r.headers.setdefault("Content-Type", "application/json")
            if use_orjson:
                try:
                    return _orjson.dumps(
                        r.media, default=orjson_hook, option=_ORJSON_OPTIONS
                    )
                except TypeError:
                    # orjson is stricter than the stdlib in a few corners
                    # (e.g. integers beyond 64 bits); fall back rather than
                    # regress on payloads the stdlib can serialize.
                    pass
            return json.dumps(r.media, default=hook, ensure_ascii=ensure_ascii)
        content = await r.content
        # Decoding always uses the stdlib. orjson is kept for ENCODING only:
        # on decode it silently parses integers beyond 64 bits as lossy floats
        # (``2**64`` -> ``1.84e19``) instead of raising, where the stdlib
        # returns the exact ``int``. It also rejects ``NaN``/``Infinity``
        # literals the stdlib accepts. Both differences are avoided by decoding
        # with ``json.loads``.
        try:
            return json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    # Expose the composed default= hook so other JSON emitters (e.g.
    # Response.problem) serialize the same types the media path does,
    # including any user-supplied API(encoder=...).
    format_json._responder_default_hook = hook  # type: ignore[attr-defined]
    return format_json


async def format_files(r, encode=False):
    """The uploaded files, as ``{name: UploadFile}`` (streamed, spooled to disk).

    As of Responder 6.0 this returns Starlette ``UploadFile`` objects instead of
    a fully-buffered bytes-dict. ``File()`` markers are the typed equivalent.
    """
    if encode:
        return None
    form = await r._parsed_form()
    return {
        key: value for key, value in form.multi_items() if not isinstance(value, str)
    }


def _make_msgpack_format(hook):
    async def format_msgpack(r, encode=False):
        try:
            import msgpack
        except ImportError as exc:
            raise ImportError(
                "msgpack is required for MessagePack support: pip install msgpack"
            ) from exc

        if encode:
            r.headers.setdefault("Content-Type", "application/x-msgpack")
            return msgpack.packb(r.media, default=hook)
        try:
            return msgpack.unpackb(await r.content)
        except (ValueError, msgpack.exceptions.UnpackException) as exc:
            raise HTTPException(
                status_code=400, detail="Invalid MessagePack body"
            ) from exc

    return format_msgpack


def get_formats(encoder=None, json_ensure_ascii=False, json_decimal="string"):
    """Return the content-negotiation formatters.

    :param encoder: Optional ``obj -> serializable`` callable applied across
        **all** response formats (JSON, YAML, MessagePack) to convert otherwise
        unserializable objects. It is tried first and falls back to the built-in
        conversions (datetime/date/time/UUID/Decimal/set/dataclass/Pydantic
        model). ``None`` uses only the built-ins.
    :param json_ensure_ascii: If ``True``, JSON escapes non-ASCII as
        ``\\uXXXX``; ``False`` (the default since 6.0) emits raw UTF-8.
    :param json_decimal: ``"string"`` (default) serializes Decimal values as
        precision-preserving strings; ``"float"`` preserves the legacy lossy
        conversion and deprecation warning.

    When `orjson <https://github.com/ijl/orjson>`_ is installed (e.g. via the
    ``responder[orjson]`` extra), the JSON format transparently uses it for
    **encoding** — typically 3-10x faster than the stdlib and less time spent
    blocking the event loop. The ``json_ensure_ascii=True`` path always uses
    the stdlib, since orjson emits UTF-8 only. Encoded output differs from the
    stdlib only in whitespace (orjson emits compact separators), except that
    float ``nan``/``inf`` values serialize as ``null`` instead of the
    non-standard ``NaN``/``Infinity`` literals. **Decoding** always uses the
    stdlib ``json.loads``: orjson decodes integers beyond 64 bits as lossy
    floats rather than exact ``int`` values, and rejects ``NaN``/``Infinity``
    literals, so the stdlib decoder is kept for correctness and parity.
    """
    hook = _make_default_hook(encoder, json_decimal=json_decimal)
    return {
        "json": _make_json_format(hook, ensure_ascii=json_ensure_ascii),
        "yaml": _make_yaml_format(hook),
        "form": format_form,
        "files": format_files,
        "msgpack": _make_msgpack_format(hook),
    }
