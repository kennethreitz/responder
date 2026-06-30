"""A minimal marimo notebook used by examples/marimo_mount.py."""

from __future__ import annotations

import marimo

app = marimo.App()


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _(mo):
    mo.md(
        """
        # Hello from marimo

        This notebook is mounted inside a Responder application.
        """
    )


if __name__ == "__main__":
    app.run()
