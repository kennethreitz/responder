import importlib
import importlib.util
import logging
import sys
import typing as t
from pathlib import Path

try:
    from pueblo.sfa.core import InvalidTarget, SingleFileApplication
except ImportError:
    SingleFileApplication = None

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
        module_name = _unique_module_name(path.stem)
        module_spec = importlib.util.spec_from_file_location(module_name, path)
        if module_spec is None or module_spec.loader is None:
            raise ImportError(f"Cannot load module from file: {spec}")
        module = importlib.util.module_from_spec(module_spec)
        # Register the module before executing it (standard importlib recipe)
        # so code inside the app that relies on its own module being findable
        # by name — dataclasses, pickle, typing.get_type_hints — works.
        sys.modules[module_name] = module
        try:
            module_spec.loader.exec_module(module)
        except BaseException:
            # Remove only the entry this loader added; never evict a module
            # that was already imported under the same name.
            if sys.modules.get(module_name) is module:
                del sys.modules[module_name]
            raise
    else:
        module = importlib.import_module(spec)
    return getattr(module, prop)


def _unique_module_name(stem: str) -> str:
    """A ``sys.modules`` key for a file-based target that never clobbers an
    already-imported module (e.g. an app file named ``json.py``).

    The chosen name is also the module's ``__name__`` (via
    ``spec_from_file_location``), so dataclasses/pickle keep working.
    """
    if stem not in sys.modules:
        return stem
    candidate = f"_responder_target_{stem}"
    counter = 1
    while candidate in sys.modules:
        counter += 1
        candidate = f"_responder_target_{stem}_{counter}"
    return candidate
