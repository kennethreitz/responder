# ruff: noqa: S603  # Subprocess call - output not captured
# ruff: noqa: S607  # Starting a process with a partial executable path
# Security considerations for subprocess usage:
# 1. Only execute the 'responder' binary from PATH
# 2. Validate all user inputs before passing to subprocess
# 3. Use Path.resolve() to prevent path traversal
import functools
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
    Utility class for managing Responder program execution.

    This class provides methods for:
    - Locating the responder executable in PATH
    - Building frontend assets using npm

    Example:
        >>> program_path = ResponderProgram.path()
        >>> build_status = ResponderProgram.build(Path("app_dir"))
    """

    @staticmethod
    @functools.lru_cache(maxsize=None)
    def path():
        name = "responder"
        if sys.platform == "win32":
            name = "responder.exe"
        program = shutil.which(name)
        if program is None:
            paths = os.environ.get("PATH", "").split(os.pathsep)
            raise RuntimeError(
                f"Could not find '{name}' executable in PATH. "
                f"Please install Responder with 'pip install --upgrade responder'. "
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

    def __init__(
        self, target: str, port: int = 5042, limit_max_requests: int | None = None
    ):
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

        # Check if port is available.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("localhost", port))
        except OSError as ex:
            raise ValueError(f"Port {port} is already in use") from ex

        # Instance variables after validation.
        self.target = target
        self.port = port
        self.limit_max_requests = limit_max_requests
        self.shutdown_timeout = 5  # seconds

        # Allow the thread to be terminated when the main program exits.
        self.process: subprocess.Popen | None = None
        self.daemon = True
        self._process_lock = threading.Lock()

        # Signal handlers are installed by ``start()`` (main thread only) and
        # restored by ``stop()``; ``signal.signal`` raises ValueError anywhere
        # but the main thread, and installing eagerly in ``__init__`` would
        # clobber the host application's handlers before the server even runs.
        self._previous_handlers: dict[int, object] = {}
        # The exact handler object passed to ``signal.signal``, kept so
        # ``_restore_signal_handlers`` can tell (by identity) whether this
        # instance's handler is still the one installed.
        self._installed_handler: object | None = None

    def start(self):
        """Start the server thread, installing signal handlers first."""
        self._install_signal_handlers()
        super().start()

    def _install_signal_handlers(self):
        """Install SIGTERM/SIGINT handlers, remembering the previous ones.

        A no-op off the main thread, where ``signal.signal`` is unavailable.
        """
        if threading.current_thread() is not threading.main_thread():
            return
        # Bind the handler once so ``signal.getsignal`` later returns this
        # exact object and identity comparison in the restore path works.
        handler = self._signal_handler
        self._installed_handler = handler
        self._previous_handlers = {
            signal.SIGTERM: signal.signal(signal.SIGTERM, handler),
            signal.SIGINT: signal.signal(signal.SIGINT, handler),
        }

    def _restore_signal_handlers(self):
        """Restore the signal handlers that were replaced by ``start()``.

        A signal is only restored when this instance's handler is still the
        one installed; if another handler took over since (e.g. a second
        server started later), it is left in place — and our saved handlers
        are kept so that server can unwind past us. When restoring, saved
        handlers belonging to servers that have already stopped are skipped,
        so a dead server's handler (which swallows signals) is never
        resurrected after non-LIFO stops.
        """
        if threading.current_thread() is not threading.main_thread():
            return
        for signum in list(self._previous_handlers):
            if signal.getsignal(signum) is not self._installed_handler:
                continue
            handler = self._unwind_handler(signum, self._previous_handlers.pop(signum))
            signal.signal(signum, handler)  # type: ignore[arg-type]

    @staticmethod
    def _unwind_handler(signum: int, handler: object) -> object:
        """Follow a chain of saved handlers past servers that already stopped."""
        seen: set[int] = set()
        while True:
            owner = getattr(handler, "__self__", None)
            if (
                not isinstance(owner, ResponderServer)
                or not owner._stopping
                or id(owner) in seen
                or signum not in owner._previous_handlers
            ):
                return handler
            seen.add(id(owner))
            handler = owner._previous_handlers[signum]

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

        with self._process_lock:
            self.process = subprocess.Popen(
                command,
                env=env,
                universal_newlines=True,
            )
        self.process.wait()

    def stop(self):
        """
        Gracefully stop the process (API).
        """
        if self._stopping:
            return
        with self._process_lock:
            self._stop()
        self._restore_signal_handlers()

    def _stop(self):
        """
        Gracefully stop the process (impl).
        """
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

    def wait_until_ready(
        self, timeout: int = 30, request_timeout: int = 1, delay: float = 0.1
    ) -> bool:
        """
        Wait until the server is ready to accept connections.

        Args:
            timeout (int, optional): Maximum time to wait in seconds. Defaults to 30.

        Returns:
            bool: True if server is ready and accepting connections, False otherwise.
        """
        start_time = time.time()
        last_error = None
        while time.time() - start_time < timeout:
            if self.process is None:
                # ``run()`` has not spawned the subprocess yet. Keep waiting
                # while the thread is coming up; give up if it never started.
                if not self.is_alive():
                    logger.error("Server process was never started")
                    return False
                time.sleep(delay)
                continue
            if not self.is_running():
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
                last_error = ex
                logger.debug(f"Server not ready yet: {ex}")
                time.sleep(delay)
        logger.error(
            "Server failed to start within %d seconds. Last error: %s",
            timeout,
            last_error,
        )
        return False

    def is_running(self):
        """
        Check if the server process is still running.
        """
        return self.process is not None and self.process.poll() is None
