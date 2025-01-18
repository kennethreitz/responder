import logging
import typing as t

from pueblo.sfa.core import InvalidTarget, SingleFileApplication

__all__ = [
    "InvalidTarget",
    "SingleFileApplication",
    "load_target",
]

logger = logging.getLogger(__name__)


def load_target(target: str, default_property: str = "api", method: str = "run") -> t.Any:
    """
    Load Python code from a file path or module name.

    Warning:
        This function executes arbitrary Python code. Ensure the target is from a trusted
        source to prevent security vulnerabilities.

    Args:
        target: Module address (e.g., 'acme.app:foo'), file path (e.g., '/path/to/acme/app.py'),
                or URL.
        default_property: Name of the property to load if not specified in target (default: "api")
        method: Name of the method to invoke on the API instance (default: "run")

    Returns:
        The API instance, loaded from the given property.

    Raises:
        ValueError: If target format is invalid
        ImportError: If module cannot be imported
        AttributeError: If property or method is not found

    Example:
        >>> api = load_target("myapp.api:server")
        >>> api.run()
    """  # noqa: E501

    app = SingleFileApplication.from_spec(spec=target, default_property=default_property)
    app.load()
    return app.entrypoint
