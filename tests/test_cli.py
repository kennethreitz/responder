import subprocess

import pytest

from responder.__version__ import __version__

pytest.importorskip("docopt", reason="docopt-ng package not installed")


def test_cli_version(capfd):
    # S603, S607 are suppressed as we're using fixed arguments, not user input
    try:
        subprocess.check_call(["responder", "--version"])  # noqa: S603, S607
    except subprocess.CalledProcessError as ex:
        pytest.fail(f"CLI command failed with exit code {ex.returncode}")

    stdout = capfd.readouterr().out.strip()
    assert stdout == __version__
