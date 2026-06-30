"""A small marimo dashboard mounted by examples/marimo_mount.py."""

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
        # Responder + marimo

        This notebook is served from a Responder app at `/notebooks/`.
        It is deliberately dependency-light, so it can ship with the examples
        and still show how a notebook can act as a small operational dashboard.
        """
    )


@app.cell
def _():
    from datetime import UTC, datetime

    checked_at = datetime.now(UTC).replace(microsecond=0)
    routes = [
        {
            "method": "GET",
            "path": "/hello",
            "purpose": "JSON endpoint served by Responder",
        },
        {
            "method": "GET",
            "path": "/docs",
            "purpose": "Interactive OpenAPI documentation",
        },
        {
            "method": "GET",
            "path": "/notebooks/",
            "purpose": "This mounted marimo notebook",
        },
    ]
    return checked_at, routes


@app.cell
def _(checked_at, mo, routes):
    rows = "\n".join(
        f"| `{route['method']}` | `{route['path']}` | {route['purpose']} |"
        for route in routes
    )
    mo.md(
        f"""
        ## Mounted App Snapshot

        Last rendered at `{checked_at.isoformat()}`.

        | Method | Path | Purpose |
        | --- | --- | --- |
        {rows}
        """
    )


@app.cell
def _():
    checks = [
        ("Responder app imports cleanly", True, "The ASGI app is import-safe."),
        ("Notebook file exists", True, "Mounted from `examples/notebooks/hello.py`."),
        ("Optional dependency boundary", True, "`marimo` is required only to run it."),
        ("OpenAPI docs enabled", True, "The parent app exposes `/docs`."),
    ]
    return (checks,)


@app.cell
def _(checks, mo):
    rows = "\n".join(
        f"| {name} | {'PASS' if ok else 'CHECK'} | {note} |"
        for name, ok, note in checks
    )
    mo.md(
        f"""
        ## Readiness Checks

        | Check | Status | Notes |
        | --- | --- | --- |
        {rows}
        """
    )


@app.cell
def _():
    sample_requests = [
        ("/hello", 18),
        ("/docs", 7),
        ("/notebooks/", 11),
        ("/schema.yml", 4),
    ]
    max_count = max(count for _, count in sample_requests)

    def bar(count: int, *, width: int = 24) -> str:
        filled = round((count / max_count) * width)
        return "#" * filled

    request_rows = [
        (path, count, bar(count)) for path, count in sample_requests
    ]
    return (request_rows,)


@app.cell
def _(mo, request_rows):
    rows = "\n".join(
        f"| `{path}` | {count} | `{bar}` |"
        for path, count, bar in request_rows
    )
    mo.md(
        f"""
        ## Tiny Traffic Sketch

        These numbers are sample data for the notebook example. Replace the
        list in the previous cell with live metrics when you mount marimo in a
        real service.

        | Path | Requests | Shape |
        | --- | ---: | --- |
        {rows}
        """
    )


@app.cell
def _(mo):
    mo.md(
        """
        ## Try It

        ```bash
        curl http://127.0.0.1:5042/hello
        open http://127.0.0.1:5042/docs
        open http://127.0.0.1:5042/notebooks/
        ```
        """
    )


if __name__ == "__main__":
    app.run()
