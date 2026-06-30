"""Mount marimo notebooks inside a Responder API.

Requirements:
    pip install responder marimo

Run it:
    python examples/marimo_mount.py

Then visit:
    http://127.0.0.1:5042/
    http://127.0.0.1:5042/hello
    http://127.0.0.1:5042/notebooks/
"""

from __future__ import annotations

from pathlib import Path

import responder

try:
    import marimo
except ImportError:  # pragma: no cover - exercised when the optional extra is absent
    marimo = None


NOTEBOOK_PATH = Path(__file__).with_name("notebooks") / "hello.py"


def create_api(
    *,
    notebook_path: Path | str = NOTEBOOK_PATH,
    mount_notebooks: bool = True,
    marimo_module=None,
) -> responder.API:
    api = responder.API(
        title="Responder + marimo",
        version="1.0",
        openapi="3.1.0",
        docs_route="/docs",
        sessions=False,
    )

    @api.get("/", include_in_schema=False)
    def index(req, resp):
        resp.redirect("/notebooks/")

    @api.get("/hello")
    def hello(req, resp):
        resp.media = {
            "message": "Hello from Responder!",
            "notebooks": "/notebooks/",
        }

    if not mount_notebooks:
        return api

    marimo_runtime = marimo if marimo_module is None else marimo_module

    if marimo_runtime is None:
        @api.get("/notebooks", include_in_schema=False)
        def missing_marimo(req, resp):
            resp.problem(
                503,
                "Install marimo to run the notebook mount example.",
                type="https://responder.example/problems/marimo-unavailable",
            )

        @api.get("/notebooks/{path:path}", include_in_schema=False)
        def missing_marimo_path(req, resp, *, path: str):
            resp.problem(
                503,
                "Install marimo to run the notebook mount example.",
                type="https://responder.example/problems/marimo-unavailable",
            )

        return api

    server = marimo_runtime.create_asgi_app().with_app(
        path="/notebooks",
        root=str(Path(notebook_path)),
    )
    api.mount("", server.build())
    return api


api = create_api()


if __name__ == "__main__":
    api.run()
