from importlib.metadata import PackageNotFoundError, version

from . import ext
from .core import API, Request, Response

try:
    __version__ = version("responder")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "unknown"

__all__ = [
    "API",
    "Request",
    "Response",
    "ext",
    "__version__",
]
