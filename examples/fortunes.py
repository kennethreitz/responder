"""Serve fortunes from the local fortune command.

The `fortune` CLI must be installed and available on PATH to run this example.
Set FORTUNE_COMMAND to use a compatible command with extra arguments.

Run it:

    responder run examples/fortunes.py

Try it with:

    curl http://127.0.0.1:5042/fortune
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Callable

from pydantic import BaseModel, Field

import responder


class FortuneUnavailable(RuntimeError):
    """Raised when the fortune command cannot produce a fortune."""


class FortuneOut(BaseModel):
    fortune: str = Field(examples=["You will write clear, useful examples."])
    source: str = Field(examples=["fortune"])


def read_fortune(command: str | None = None) -> str:
    command = command or os.environ.get("FORTUNE_COMMAND", "fortune")
    args = shlex.split(command)
    if not args:
        raise FortuneUnavailable("FORTUNE_COMMAND did not include an executable.")

    executable = shutil.which(args[0])
    if executable is None:
        raise FortuneUnavailable(
            f"Install {args[0]!r} or set FORTUNE_COMMAND to a compatible CLI."
        )
    args[0] = executable

    try:
        result = subprocess.run(  # noqa: S603 - command is resolved without shell.
            args,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except subprocess.TimeoutExpired as exc:
        raise FortuneUnavailable(f"{command!r} timed out.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        if detail:
            raise FortuneUnavailable(
                f"{command!r} exited with status {exc.returncode}: {detail}"
            ) from exc
        raise FortuneUnavailable(
            f"{command!r} exited with status {exc.returncode}."
        ) from exc

    fortune = result.stdout.strip()
    if not fortune:
        raise FortuneUnavailable(f"{command!r} returned an empty fortune.")
    return fortune


def create_api(
    *,
    fortune_reader: Callable[[], str] | None = None,
    command: str | None = None,
) -> responder.API:
    command = command or os.environ.get("FORTUNE_COMMAND", "fortune")
    fortune_reader = fortune_reader or (lambda: read_fortune(command))

    api = responder.API(
        title="Fortunes API",
        version="1.0",
        openapi="3.1.0",
        docs_route="/docs",
        sessions=False,
    )

    @api.get("/", include_in_schema=False)
    def index(req, resp):
        resp.media = {"name": "Fortunes API", "fortune": "/fortune"}

    @api.get(
        "/fortune",
        operation_id="get_fortune",
        tags=["fortunes"],
        summary="Read a fortune",
        description="Run the local fortune command and return its output.",
        response_model=FortuneOut,
        responses={503: "Fortune command unavailable"},
        examples={
            "example": {
                "value": {
                    "fortune": "You will write clear, useful examples.",
                    "source": "fortune",
                }
            }
        },
    )
    def get_fortune(req, resp):
        try:
            fortune = fortune_reader()
        except FortuneUnavailable as exc:
            resp.problem(
                503,
                str(exc),
                type="https://responder.example/problems/fortune-unavailable",
                command=command,
            )
            return

        resp.media = FortuneOut(fortune=fortune, source=command)

    return api


api = create_api()


if __name__ == "__main__":
    api.run()
