import importlib
import importlib.util
import logging
import sys
import typing as t
import uuid
from pathlib import Path
from types import ModuleType

logger = logging.getLogger(__name__)


class InvalidTarget(Exception):
    """
    Raised when the target specification format is invalid.

    This exception is raised when the target string does not conform to the expected
    format of either a module path (e.g., 'acme.app:foo') or a file path
    (e.g., '/path/to/acme/app.py').
    """

    pass


def load_target(target: str, default_property: str = "api", method: str = "run") -> t.Any:
    """
    Load Python code from a file path or module name.

    Warning:
        This function executes arbitrary Python code. Ensure the target is from a trusted
        source to prevent security vulnerabilities.

    Args:
        target: Module address (e.g., 'acme.app:foo') or file path (e.g., '/path/to/acme/app.py')
        default_property: Name of the property to load if not specified in target (default: "api")
        method: Name of the method to verify on the loaded property (default: "run")

    Returns:
        The loaded property from the module

    Raises:
        ValueError: If target format is invalid
        ImportError: If module cannot be imported
        AttributeError: If property or method is not found

    Example:
        >>> api = load_target("myapp.api:server")
        >>> api.run()
    """  # noqa: E501

    # Sanity checks, as suggested by @coderabbitai. Thanks.
    if not target or (":" in target and len(target.split(":")) != 2):
        raise InvalidTarget(f"Invalid target format: {target}")

    # Decode launch target location address.
    # Module: acme.app:foo
    # Path:   /path/to/acme/app.py:foo
    target_fragments = target.split(":")
    if len(target_fragments) > 1:
        target = target_fragments[0]
        prop = target_fragments[1]
    else:
        prop = default_property

    # Validate property name follows Python identifier rules.
    if not prop.isidentifier():
        raise ValueError(f"Invalid property name: {prop}")

    # Import launch target. Treat input location either as a filesystem path
    # (/path/to/acme/app.py), or as a module address specification (acme.app).
    path = Path(target)
    if path.is_file():
        app = load_file_module(path)
    else:
        app = importlib.import_module(target)

    # Invoke launch target.
    msg_prefix = f"Failed to import target '{target}'"
    try:
        api = getattr(app, prop, None)
        if api is None:
            raise AttributeError(f"Module has no API instance attribute '{prop}'")
        if not hasattr(api, method):
            raise AttributeError(f"API instance '{prop}' has no method '{method}'")
        return api
    except ImportError as ex:
        raise ImportError(f"{msg_prefix}: {ex}") from ex
    except AttributeError as ex:
        raise AttributeError(f"{msg_prefix}: {ex}") from ex
    except Exception as ex:
        raise RuntimeError(f"{msg_prefix}: Unexpected error: {ex}") from ex


def load_file_module(path: Path) -> ModuleType:
    """
    Load a Python file as a module using importlib.

    Args:
        path: Path to the Python file to load

    Returns:
        The loaded module object

    Raises:
        ImportError: If the module cannot be loaded
    """

    # Validate file extension
    if path.suffix != ".py":
        raise ValueError(f"File must have .py extension: {path}")

    # Use unique surrogate module name.
    unique_id = uuid.uuid4().hex
    name = f"__{path.stem}_{unique_id}__"

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Failed loading module from file: {path}")
    app = importlib.util.module_from_spec(spec)
    sys.modules[name] = app
    try:
        spec.loader.exec_module(app)
        return app
    except (ImportError, SyntaxError) as ex:
        sys.modules.pop(name, None)
        raise ImportError(
            f"Failed to execute module '{app}': {ex.__class__.__name__}: {ex}"
        ) from ex
    except Exception as ex:
        sys.modules.pop(name, None)
        raise RuntimeError(
            f"Unexpected error executing module '{app}': {ex.__class__.__name__}: {ex}"
        ) from ex
