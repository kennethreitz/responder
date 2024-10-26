"""
Responder CLI.

A web framework for Python.

Commands:
  run     Start the application server
  build   Build frontend assets using npm

Usage:
  responder
  responder run [--debug] [--limit-max-requests=] <target>
  responder build [<target>]
  responder --version

Options:
  -h --help     Show this screen.
  -v --version  Show version.
  --debug       Enable debug mode with verbose logging.
  --limit-max-requests=<n>  Maximum number of requests to handle before shutting down.

Arguments:
  <target>      For run: Python module specifier (e.g., "app:api" loads api from app.py)
                         Format: "module.submodule:variable_name" where variable_name is your API instance
                For build: Directory containing package.json (default: current directory)

Examples:
  responder run app:api                     # Run the 'api' instance from app.py
  responder run myapp/core.py:application   # Run the 'application' instance from myapp/core.py
  responder build                           # Build frontend assets
"""  # noqa: E501

import logging
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

    if build:
        target_path = Path(target).resolve() if target else Path.cwd()
        if not target_path.is_dir() or not (target_path / "package.json").exists():
            logger.error(
                f"Invalid target directory or missing package.json: {target_path}"
            )
            sys.exit(1)
        npm_cmd = "npm.cmd" if platform.system() == "Windows" else "npm"
        try:
            # # S603, S607 are addressed by validating the target directory.
            subprocess.check_call(  # noqa: S603, S607
                [npm_cmd, "run", "build"],
                cwd=target_path,
                timeout=300,
            )
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

        # Launch Responder API server (uvicorn).
        api.run(debug=debug, limit_max_requests=limit_max_requests)


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
