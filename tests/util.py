"""
Utility functions for testing server components.

This module provides functions for managing test server instances,
including port allocation and server readiness checking.
"""

import errno
import logging
import socket
import time
import typing as t
from copy import copy
from functools import lru_cache

import requests

logger = logging.getLogger(__name__)


def random_port() -> int:
    """
    Return a random available port by binding to port 0.

    Returns:
        int: An available port number that can be used for testing.
    """
    sock = socket.socket()
    try:
        sock.bind(("", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


@lru_cache(maxsize=None)
def transient_socket_error_numbers() -> t.List[int]:
    """
    A list of TCP socket error numbers to ignore in `wait_server_tcp`.

    On Windows, Winsock error codes are the Unix error code + 10000.

    Returns:
        List[int]: A list containing both Unix and Windows-specific error codes.
        For each Unix error code 'x', includes both 'x' and 'x + 10000'.
    """
    error_numbers = [
        errno.EAGAIN,
        errno.ECONNABORTED,
        errno.ECONNREFUSED,
        errno.ETIMEDOUT,
        errno.EWOULDBLOCK,
    ]
    error_numbers_effective = copy(error_numbers)
    error_numbers_effective.extend(error_number + 10000 for error_number in error_numbers)
    return error_numbers_effective


def wait_server_tcp(
    port: int,
    host: str = "127.0.0.1",
    timeout: int = 10,
    delay: float = 0.1,
) -> None:
    """
    Wait for server to be ready by attempting TCP connections.

    Args:
        port: The port number to connect to
        host: The host to connect to (default: "127.0.0.1")
        timeout: Maximum time to wait in seconds (default: 10)
        delay: Delay between attempts in seconds (default: 0.1)

    Raises:
        RuntimeError: If server is not ready within timeout period
    """
    endpoint = f"tcp://{host}:{port}/"
    logger.debug(f"Waiting for endpoint: {endpoint}")
    start_time = time.time()
    while time.time() - start_time < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(delay / 2)  # Set socket timeout
            error_number = sock.connect_ex((host, port))
            if error_number == 0:
                break

            # Expected errors when server is not ready.
            if error_number in transient_socket_error_numbers():
                pass

            # Unexpected error.
            else:
                raise RuntimeError(
                    f"Unexpected error while connecting to {endpoint}: {error_number}"
                )
        time.sleep(delay)
    else:
        raise RuntimeError(
            f"Server at {endpoint} failed to start within {timeout} seconds"
        )


def wait_server_http(
    port: int,
    host: str = "127.0.0.1",
    protocol: str = "http",
    attempts: int = 20,
    delay: float = 0.1,
) -> None:
    """
    Wait for server to be ready by attempting to connect to it.

    Args:
        port: The port number to connect to
        host: The host to connect to (default: "127.0.0.1")
        protocol: The protocol to use (default: "http")
        attempts: Number of connection attempts (default: 20)
        delay: Delay per attempt in seconds (default: 0.1)

    Raises:
        RuntimeError: If server is not ready after all attempts
    """
    url = f"{protocol}://{host}:{port}/"
    for attempt in range(1, attempts + 1):
        try:
            requests.get(url, timeout=delay / 2)  # Shorter timeout for connection
            break
        except requests.exceptions.RequestException:
            if attempt < attempts:  # Don't sleep on last attempt
                time.sleep(delay)
    else:
        raise RuntimeError(
            f"Server at {url} failed to respond after {attempts} attempts "
            f"(total wait time: {attempts * delay:.1f}s)"
        )
