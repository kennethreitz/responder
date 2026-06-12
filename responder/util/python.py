import importlib
import importlib.util
import logging
import typing as t
from pathlib import Path

try:
    from pueblo.sfa.core import InvalidTarget, SingleFileApplication
except ImportError:
    SingleFileApplication = None  # type: ignore[assignment, misc]

    class InvalidTarget(Exception):  # type: ignore[no-redef]
        """Raised when an application target specification cannot be parsed."""


__all__ = [
    "InvalidTarget",
    "SingleFileApplication",
    "load_target",
]

logger = logging.getLogger(__name__)


def load_target(target: str, default_property: str = "api") -> t.Any:
    """
    Load Python code from a file path or module name.

    Warning:
        This function executes arbitrary Python code. Ensure the target is from a trusted
        source to prevent security vulnerabilities.

    Args:
        target: Module address (e.g., 'acme.app:foo'), file path (e.g., '/path/to/acme/app.py'),
                or URL (requires the `cli` extra).
        default_property: Name of the property to load if not specified in target (default: "api")

    Returns:
        The API instance, loaded from the given property.

    Raises:
        ValueError: If target format is invalid
        ImportError: If module cannot be imported, or the target is a URL and the
            `cli` extra is not installed
        AttributeError: If property is not found

    Example:
        >>> api = load_target("myapp.api:server")
        >>> api.run()
    """  # noqa: E501

    if SingleFileApplication is not None:
        app = SingleFileApplication.from_spec(
            spec=target, default_property=default_property
        )
        app.load()
        return app.entrypoint
    return _load_target_basic(target, default_property)


def _load_target_basic(target: str, default_property: str) -> t.Any:
    """
    Load a target from a local module or file path, without pueblo.

    Supports 'module:attr', 'module', 'path/to/app.py', and 'path/to/app.py:attr'.
    Remote URL targets require pueblo, available via ``pip install 'responder[cli]'``.
    """
    if "://" in target:
        raise ImportError(
            f"Loading remote application targets requires the 'cli' extra. "
            f"Install it with: pip install 'responder[cli]' (target: {target})"
        )

    spec, _, prop = target.partition(":")
    prop = prop or default_property
    if not spec:
        raise InvalidTarget(f"Invalid target: {target}")

    path = Path(spec)
    if spec.endswith(".py") or path.is_file():
        module_spec = importlib.util.spec_from_file_location(path.stem, path)
        if module_spec is None or module_spec.loader is None:
            raise ImportError(f"Cannot load module from file: {spec}")
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
    else:
        module = importlib.import_module(spec)
    return getattr(module, prop)
