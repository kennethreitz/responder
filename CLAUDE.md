# Responder

A familiar HTTP Service Framework for Python, by Kenneth Reitz.

## Commands

- **Tests**: `uv run pytest` (runs full suite with coverage)
- **Single test**: `uv run pytest tests/test_responder.py::test_name -xvs`
- **Lint**: `uv run ruff check .`
- **Type check**: `uv run mypy`
- **Build docs**: `cd docs && uv run make html`
- **Build package**: `uv build`
- **Lock deps**: `uv lock`

## Architecture

- `responder/api.py` — Main `API` class, the entry point for all apps
- `responder/routes.py` — `Router`, `Route`, `WebSocketRoute` dispatch
- `responder/models.py` — `Request` and `Response` wrappers around Starlette
- `responder/ext/` — Extensions: CLI, GraphQL, OpenAPI, rate limiting
- `responder/background.py` — Background task queue
- `responder/formats.py` — Content negotiation (JSON, YAML, msgpack)
- `responder/__version__.py` — Single source of truth for version string

## Conventions

- Python 3.10+ only. Use `from __future__ import annotations` where present.
- Use `inspect.iscoroutinefunction` (not `asyncio.iscoroutinefunction`).
- Tests use `api.requests` (Starlette TestClient) with `allowed_hosts=[";"]` or `["localhost"]`.
- Werkzeug 3.1.7+ rejects invalid Host headers — use `localhost` when mounting WSGI apps in tests.
- Version is in `responder/__version__.py`, bump it there.
- Changelog follows [Keep a Changelog](https://keepachangelog.com/) format in `CHANGELOG.md`.
- Compare links at the bottom of CHANGELOG.md must be updated when adding a release.
- All deps managed via `uv`. Lock file (`uv.lock`) is committed.

## Release Process

1. Bump version in `responder/__version__.py`
2. Add changelog entry in `CHANGELOG.md` (update compare links too)
3. `uv lock` to refresh the lock file
4. Commit: `Bump version to X.Y.Z and update changelog`
5. `git tag vX.Y.Z && git push && git push origin vX.Y.Z`
6. `gh release create vX.Y.Z --title "vX.Y.Z" --notes "..."`
7. `uv build && uvx twine upload dist/responder-X.Y.Z*`
