"""
Test module for Responder CLI functionality.

This module tests the following CLI commands:
- responder --version: Version display
- responder build: Build command execution
- responder run: Server execution

Requirements:
- The `docopt` package must be installed
- Example application must be present at `examples/helloworld.py`
- This file should implement a basic HTTP server with a "/hello" endpoint
  that returns "hello, world!" as response
"""

import json
import os
import subprocess
import time
import typing as t
from pathlib import Path

import pytest
import requests
from _pytest.capture import CaptureFixture
from requests_toolbelt.multipart.encoder import to_list

from responder.__version__ import __version__
from responder.util.cmd import ResponderProgram, ResponderServer
from tests.util import random_port, wait_server_tcp

# Skip test if optional CLI dependency is not installed.
pytest.importorskip("docopt", reason="docopt-ng package not installed")


# Pseudo-wait for server idleness
SERVER_IDLE_WAIT = float(os.getenv("RESPONDER_SERVER_IDLE_WAIT", "0.25"))

# Maximum time to wait for server startup or teardown (adjust for slower systems)
SERVER_TIMEOUT = float(os.getenv("RESPONDER_SERVER_TIMEOUT", "5"))

# Maximum time to wait for HTTP requests (adjust for slower networks)
REQUEST_TIMEOUT = float(os.getenv("RESPONDER_REQUEST_TIMEOUT", "5"))

# Endpoint to use for `responder run`.
HELLO_ENDPOINT = "/hello"


def test_cli_version(capfd):
    """
    Verify that `responder --version` works as expected.
    """
    try:
        # Suppress security checks for subprocess calls in tests.
        # S603: subprocess call - safe as we use fixed command
        # S607: start process with partial path - safe as we use installed package
        subprocess.check_call(["responder", "--version"])  # noqa: S603, S607
    except subprocess.CalledProcessError as ex:
        pytest.fail(
            f"responder --version failed with exit code {ex.returncode}. Error: {ex}"
        )

    stdout = capfd.readouterr().out.strip()
    assert stdout == __version__


def responder_build(path: Path, capfd: CaptureFixture) -> t.Tuple[str, str]:
    """
    Execute responder build command and capture its output.

    Args:
        path: Directory containing package.json
        capfd: Pytest fixture for capturing output

    Returns:
        tuple: (stdout, stderr) containing the captured output
    """

    ResponderProgram.build(path=path)
    output = capfd.readouterr()

    stdout = output.out.strip()
    stderr = output.err.strip()

    return stdout, stderr


def test_cli_build_success(capfd, tmp_path):
    """
    Verify that `responder build` works as expected.
    """

    # Temporary surrogate `package.json` file.
    package_json = {"scripts": {"build": "echo Hotzenplotz"}}
    package_json_file = tmp_path / "package.json"
    package_json_file.write_text(json.dumps(package_json))

    # Invoke `responder build`.
    stdout, stderr = responder_build(tmp_path, capfd)
    assert "Hotzenplotz" in stdout


def test_cli_build_missing_package_json(capfd, tmp_path):
    """
    Verify `responder build`, while `package.json` file is missing.
    """

    # Invoke `responder build`.
    stdout, stderr = responder_build(tmp_path, capfd)
    assert "Invalid target directory or missing package.json" in stderr


@pytest.mark.parametrize(
    "invalid_content,npm_error,expected_error",
    [
        ("foobar", "code EJSONPARSE", ["is not valid JSON", "Failed to parse JSON data"]),
        ("{", "code EJSONPARSE", "Unexpected end of JSON input"),
        ('{"scripts": }', "code EJSONPARSE", "Unexpected token"),
        (
            '{"scripts": null}',
            "Cannot convert undefined or null to object",
            "scripts.build script not found",
        ),
        ('{"scripts": {"build": null}}', "Missing script", '"build"'),
        ('{"scripts": {"build": 123}}', "Missing script", '"build"'),
    ],
    ids=[
        "invalid_json_content",
        "incomplete_json",
        "syntax_error",
        "null_scripts",
        "missing_script_null",
        "missing_script_number",
    ],
)
def test_cli_build_invalid_package_json(
    capfd, tmp_path, invalid_content, npm_error, expected_error
):
    """
    Verify `responder build` using an invalid `package.json` file.
    """

    # Temporary surrogate `package.json` file.
    package_json_file = tmp_path / "package.json"
    package_json_file.write_text(invalid_content)

    # Invoke `responder build`.
    stdout, stderr = responder_build(tmp_path, capfd)
    assert f"npm error {npm_error}" in stderr
    assert any(item in stderr for item in to_list(expected_error))


# The test is marked as flaky due to potential race conditions in server startup
# and port availability. Known error codes by platform:
# - macOS:   [Errno 61] Connection refused (Failed to establish a new connection)
# - Linux:   [Errno 111] Connection refused (Failed to establish a new connection)
# - Windows: [WinError 10061] No connection could be made because target machine
#            actively refused it
@pytest.mark.flaky(reruns=5, reruns_delay=2)
def test_cli_run(capfd):
    """
    Verify that `responder run` works as expected.
    """

    # Invoke `responder run`.
    target = Path("examples") / "helloworld.py"

    # Start a Responder service instance in the background, using its CLI.
    # Make it terminate itself after serving one HTTP request.
    server = ResponderServer(target=str(target), port=random_port(), limit_max_requests=1)
    try:
        # Start server and wait until it responds on TCP.
        server.start()
        wait_server_tcp(server.port)

        # Submit a single probing HTTP request that also will terminate the server.
        response = requests.get(
            f"http://127.0.0.1:{server.port}{HELLO_ENDPOINT}", timeout=REQUEST_TIMEOUT
        )
        assert "hello, world!" == response.text
    finally:
        server.join(timeout=SERVER_TIMEOUT)

    # Capture process output.
    time.sleep(SERVER_IDLE_WAIT)
    output = capfd.readouterr()

    stdout = output.out.strip()
    assert f'"GET {HELLO_ENDPOINT} HTTP/1.1" 200 OK' in stdout

    stderr = output.err.strip()

    # Define expected lifecycle messages in order.
    lifecycle_messages = [
        # Startup phase
        "Started server process",
        "Waiting for application startup",
        "Application startup complete",
        "Uvicorn running",
        # Shutdown phase
        "Shutting down",
        "Waiting for application shutdown",
        "Application shutdown complete",
        "Finished server process",
    ]

    # Verify messages appear in expected order.
    last_pos = -1
    for msg in lifecycle_messages:
        pos = stderr.find(msg)
        assert pos > last_pos, f"Expected '{msg}' to appear after previous message"
        last_pos = pos
