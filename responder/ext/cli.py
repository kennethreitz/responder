"""
Responder CLI.

A web framework for Python.

Commands:
  run     Start the application server
  build   Build frontend assets using npm
  client  Generate an API client from the app's OpenAPI schema

Usage:
  responder run [--debug] [--reload] [--host=<addr>] [--port=<n>] [--server=<name>] [--limit-max-requests=] <target>
  responder build [<target>]
  responder client [--lang=<lang>] [--class-name=<name>] [--output=<path>] <target>
  responder --version

Options:
  -h --help     Show this screen.
  -v --version  Show version.
  --debug       Enable debug mode with verbose logging.
  --reload      Restart the server when source files change (uvicorn only;
                requires a module target such as "app:api").
  --host=<addr>             Network address to bind to (default: 127.0.0.1).
  --port=<n>                Port to bind to (default: 5042, or $PORT when set).
                            Wins over $PORT when both are provided.
  --server=<name>           Server backend: uvicorn (default) or granian.
  --limit-max-requests=<n>  Maximum number of requests to handle before shutting down.
  --lang=<lang>             Client language: python, javascript, typescript, ruby, php [default: python].
  --class-name=<name>       Name of the generated client class [default: APIClient].
  -o --output=<path>        Write the client to this file instead of stdout.

Arguments:
  <target>      For run/client: Python module specifier (e.g., "app:api" loads api from app.py)
                         Format: "module.submodule:variable_name" where variable_name is your API instance
                For build: Directory containing package.json (default: current directory)

Examples:
  responder run app:api                     # Run the 'api' instance from app.py
  responder run myapp/core.py:application   # Run the 'application' instance from myapp/core.py
  responder run --host 0.0.0.0 --port 8000 app:api   # Bind to all interfaces on port 8000
  responder run --reload app:api            # Auto-restart on code changes (development)
  responder build                           # Build frontend assets
  responder client app:api                  # Print a Python client for app.py's api
  responder client --lang typescript -o client.ts app:api   # Write a TypeScript client
"""  # noqa: E501

import logging
import os
import platform
import subprocess
import sys
import typing as t
from pathlib import Path

import docopt

from responder.__version__ import __version__
from responder.util.python import InvalidTarget, load_target

logger = logging.getLogger(__name__)


def cli() -> None:
    """
    Main entry point for the Responder CLI.

    Parses command line arguments and executes the appropriate command.
    Supports running the application, building assets, and displaying version info.
    """
    args = docopt.docopt(__doc__, argv=None, version=__version__, options_first=False)
    setup_logging(args["--debug"])

    target: t.Optional[str] = args["<target>"]
    build: bool = args["build"]
    debug: bool = args["--debug"]
    run: bool = args["run"]
    client: bool = args["client"]

    if build:
        target_path = Path(target).resolve() if target else Path.cwd()
        if not target_path.is_dir() or not (target_path / "package.json").exists():
            logger.error(
                f"Invalid target directory or missing package.json: {target_path}"
            )
            sys.exit(1)
        npm_cmd = "npm.cmd" if platform.system() == "Windows" else "npm"
        try:
            logger.info("Starting frontend asset build")
            # S603, S607 are addressed by validating the target directory.
            subprocess.check_call(  # noqa: S603, S607
                [npm_cmd, "run", "build"],
                cwd=target_path,
                timeout=300,
            )
            logger.info("Frontend asset build completed successfully")
        except FileNotFoundError:
            logger.error("npm not found. Please install Node.js and npm.")
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            logger.error(f"Build failed with exit code {e.returncode}")
            sys.exit(1)

    if run:
        if not target:
            logger.error("Target argument is required for run command")
            sys.exit(1)

        # Maximum request limit. Terminating afterward. Suitable for software testing.
        limit_max_requests = args["--limit-max-requests"]
        if limit_max_requests is not None:
            try:
                limit_max_requests = int(limit_max_requests)
                if limit_max_requests <= 0:
                    logger.error("limit-max-requests must be a positive integer")
                    sys.exit(1)
            except ValueError:
                logger.error("limit-max-requests must be a valid integer")
                sys.exit(1)

        # Server binding and backend options.
        host: t.Optional[str] = args["--host"]
        port: t.Optional[int] = None
        if args["--port"] is not None:
            try:
                port = int(args["--port"])
            except ValueError:
                logger.error("port must be a valid integer")
                sys.exit(1)
        server: t.Optional[str] = args["--server"]

        # Granian's embedded server has no request-limit option, so forwarding
        # limit_max_requests would surface as an opaque TypeError deep in
        # api.serve. Reject the combination up front with a clear message.
        if limit_max_requests is not None and server == "granian":
            logger.error(
                "--limit-max-requests is not supported with the granian "
                "server; it is only available with uvicorn (the default)."
            )
            sys.exit(1)

        run_options: t.Dict[str, t.Any] = {}
        if host is not None:
            run_options["address"] = host
        if port is not None:
            run_options["port"] = port
        if server is not None:
            run_options["server"] = server
        if limit_max_requests is not None:
            run_options["limit_max_requests"] = limit_max_requests

        if args["--reload"]:
            _run_with_reload(target=target, debug=debug, **run_options)
            return

        # Load application from target.
        try:
            api = load_target(target=target)
        except InvalidTarget as ex:
            raise ValueError(
                f"{ex}. "
                "Use either a Python module entrypoint specification, "
                "a filesystem path, or a remote URL. "
                "See also https://responder.kennethreitz.org/cli.html."
            ) from ex

        # Launch Responder API server (uvicorn by default).
        api.run(debug=debug, **run_options)

    if client:
        if not target:
            logger.error("Target argument is required for the client command")
            sys.exit(1)

        try:
            api = load_target(target=target)
        except InvalidTarget as ex:
            raise ValueError(
                f"{ex}. "
                "Use either a Python module entrypoint specification, "
                "a filesystem path, or a remote URL. "
                "See also https://responder.kennethreitz.org/cli.html."
            ) from ex

        language = args["--lang"] or "python"
        class_name = args["--class-name"] or "APIClient"
        output = args["--output"]
        try:
            if output:
                api.generate_client(output, class_name=class_name, language=language)
                logger.info(f"Wrote {language} client to {output}")
            else:
                sys.stdout.write(
                    api.generate_client(class_name=class_name, language=language)
                )
        except (RuntimeError, ValueError, TypeError) as ex:
            logger.error(str(ex))
            sys.exit(1)


def _uvicorn_import_string(target: str) -> t.Optional[str]:
    """Translate a ``module:attr`` CLI target into a uvicorn import string.

    Returns ``None`` for targets uvicorn cannot re-import inside a reloader
    subprocess (filesystem paths and remote URLs).

    :param target: The CLI ``<target>`` argument (e.g. ``"app:api"``).
    """
    if "://" in target:
        return None
    module, _, attribute = target.partition(":")
    attribute = attribute or "api"
    if module.endswith(".py"):
        return None
    if not all(part.isidentifier() for part in module.split(".")):
        return None
    if not attribute.isidentifier():
        return None
    return f"{module}:{attribute}"


def _run_with_reload(
    *,
    target: str,
    debug: bool,
    address: t.Optional[str] = None,
    port: t.Optional[int] = None,
    server: t.Optional[str] = None,
    limit_max_requests: t.Optional[int] = None,
) -> None:
    """Serve ``target`` with uvicorn's auto-reloader (``responder run --reload``).

    Reload mode requires uvicorn to (re-)import the application itself, so the
    target must be a module specifier such as ``app:api`` — filesystem paths
    and URLs are rejected. Binding defaults mirror :meth:`responder.API.serve`
    (``127.0.0.1:5042``, or ``0.0.0.0:$PORT`` when the environment provides a
    port and no explicit ``--port`` is set).
    """
    if server not in (None, "uvicorn"):
        logger.error("--reload is only supported with the uvicorn server")
        sys.exit(1)

    app_spec = _uvicorn_import_string(target)
    if app_spec is None:
        logger.error(
            "--reload requires a module target such as 'app:api' "
            "(filesystem paths and URLs cannot be hot-reloaded)"
        )
        sys.exit(1)

    # Mirror the PORT handling and binding defaults of `API.serve`.
    if "PORT" in os.environ:
        if address is None:
            address = "0.0.0.0"  # noqa: S104
        if port is None:
            port = int(os.environ["PORT"])
    if address is None:
        address = "127.0.0.1"
    if port is None:
        port = 5042

    options: t.Dict[str, t.Any] = {}
    if debug:
        options["log_level"] = "debug"
    if limit_max_requests is not None:
        options["limit_max_requests"] = limit_max_requests

    import uvicorn

    uvicorn.run(app_spec, host=address, port=port, reload=True, **options)


def setup_logging(debug: bool) -> None:
    """
    Configure logging based on debug mode.

    Args:
        debug: When True, sets logging level to DEBUG; otherwise, sets to INFO
    """
    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=log_level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
