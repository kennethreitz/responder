# ruff: noqa: S603  # Subprocess call - output not captured
# ruff: noqa: S607  # Starting a process with a partial executable path
# Security considerations for subprocess usage:
# 1. Only execute the 'responder' binary from PATH
# 2. Validate all user inputs before passing to subprocess
# 3. Use Path.resolve() to prevent path traversal
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class ResponderProgram:
    """
    Provide full path to the `responder` program.
    """

    @staticmethod
    def path():
        name = "responder"
        if sys.platform == "win32":
            name = "responder.exe"
        program = shutil.which(name)
        if program is None:
            paths = os.environ.get("PATH", "").split(os.pathsep)
            raise RuntimeError(
                f"Could not find '{name}' executable in PATH. "
                f"Please install Responder with 'pip install --upgrade responder[cli]'. "
                f"Searched in: {', '.join(paths)}"
            )
        logger.debug(f"Found responder program: {program}")
        return program

    @classmethod
    def build(cls, path: Path) -> int:
        """
        Invoke `responder build` command.

        Args:
            path: Path to the application to build

        Returns:
            int: The return code from the build process

        Raises:
            ValueError: If the path is invalid
            RuntimeError: If the responder executable is not found
            subprocess.SubprocessError: If the build process fails
        """

        if not isinstance(path, Path):
            raise ValueError(f"Expected a Path object, got {type(path).__name__}")
        if not path.exists():
            raise ValueError(f"Path does not exist: {path}")
        if not path.is_dir():
            raise FileNotFoundError(f"Path is not a directory: {path}")

        command = [
            cls.path(),
            "build",
            str(path),
        ]
        return subprocess.call(command)


class ResponderServer(threading.Thread):
    """
    A threaded wrapper around the `responder run` command for testing purposes.

    This class allows running a Responder application in a separate thread,
    making it suitable for integration testing scenarios.

    Args:
        target (str): The path to the Responder application to run
        port (int, optional): The port to run the server on. Defaults to 5042.
        limit_max_requests (int, optional): Maximum number of requests to handle
            before shutting down. Useful for testing scenarios.

    Example:
        >>> server = ResponderServer("app.py", port=8000)
        >>> server.start()
        >>> # Run tests
        >>> server.stop()
    """

    def __init__(self, target: str, port: int = 5042, limit_max_requests: int = None):
        super().__init__()
        self._stopping = False

        # Validate input variables.
        if not target or not isinstance(target, str):
            raise ValueError("Target must be a non-empty string")
        if not isinstance(port, int) or port < 1:
            raise ValueError("Port must be a positive integer")
        if limit_max_requests is not None and (
            not isinstance(limit_max_requests, int) or limit_max_requests < 1
        ):
            raise ValueError("limit_max_requests must be a positive integer if specified")

        # Instance variables after validation.
        self.target = target
        self.port = port
        self.limit_max_requests = limit_max_requests
        self.shutdown_timeout = 5  # seconds

        # Allow the thread to be terminated when the main program exits.
        self.process: subprocess.Popen
        self.daemon = True

        # Setup signal handlers.
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def run(self):
        command = [
            ResponderProgram.path(),
            "run",
            self.target,
        ]
        if self.limit_max_requests is not None:
            command += [f"--limit-max-requests={self.limit_max_requests}"]

        # Preserve existing environment
        env = os.environ.copy()

        if self.port is not None:
            env["PORT"] = str(self.port)

        self.process = subprocess.Popen(
            command,
            env=env,
            universal_newlines=True,
        )
        self.process.wait()

    def stop(self):
        """
        Gracefully stop the process.
        """
        if self._stopping:
            return
        self._stopping = True
        if self.process and self.process.poll() is None:
            logger.info("Attempting to terminate server process...")
            self.process.terminate()
            try:
                # Wait for graceful shutdown.
                self.process.wait(timeout=self.shutdown_timeout)
                logger.info("Server process terminated gracefully")
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Server process did not terminate gracefully, forcing kill"
                )
                self.process.kill()  # Force kill if not terminated

    def _signal_handler(self, signum, frame):
        """
        Handle termination signals gracefully.
        """
        logger.info("Received signal %d, shutting down...", signum)
        self.stop()

    def wait_until_ready(self, timeout=30, request_timeout=1, delay=0.1) -> bool:
        """
        Wait until the server is ready to accept connections.

        Args:
            timeout (int, optional): Maximum time to wait in seconds. Defaults to 30.

        Returns:
            bool: True if server is ready and accepting connections, False otherwise.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            if not self.is_running():
                if self.process is None:
                    logger.error("Server process was never started")
                else:
                    returncode = self.process.poll()
                    logger.error("Server process exited with code: %d", returncode)
                return False
            try:
                with socket.create_connection(
                    ("localhost", self.port), timeout=request_timeout
                ):
                    return True
            except (
                socket.timeout,
                ConnectionRefusedError,
                socket.gaierror,
                OSError,
            ) as ex:
                logger.debug(f"Server not ready yet: {ex}")
                time.sleep(delay)
        return False

    def is_running(self):
        """
        Check if the server process is still running.
        """
        return self.process is not None and self.process.poll() is None
