"""
Responder - a familiar HTTP Service Framework.

This module exports the core functionality of the Responder framework,
including the API, Request, and Response classes.
"""

from . import ext
from .__version__ import __version__
from .core import API, Request, Response

__all__ = [
    "API",
    "Request",
    "Response",
    "__version__",
    "ext",
]
