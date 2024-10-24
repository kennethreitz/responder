from importlib.metadata import PackageNotFoundError, version

from . import ext
from .core import API, Request, Response

__appname__ = "responder"

try:
    __version__ = version(__appname__)
except PackageNotFoundError:  # pragma: no cover
    __version__ = "unknown"

__all__ = [
    "API",
    "Request",
    "Response",
    "ext",
    "__appname__",
    "__version__",
]
