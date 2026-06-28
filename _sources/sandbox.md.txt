(sandbox)=
# Development Sandbox

## Setup

Clone the repo and install all dependencies:
```shell
git clone https://github.com/kennethreitz/responder.git
cd responder
uv venv && source .venv/bin/activate
uv pip install --upgrade --editable '.[develop,docs,release,test]'
```

Working on the CLI or GraphQL extensions? Add their extras too:
```shell
uv pip install --upgrade --editable '.[cli,graphql]'
```

The commands below assume the venv is activated. If you'd rather not activate it,
prefix any command with `uv run` (e.g. `uv run pytest`, `uv run ruff check .`,
`uv run mypy`) and uv resolves the environment for you.

## Running Tests
```shell
pytest                                  # full suite with coverage
pytest tests/test_responder.py -xvs     # single file, stop on first failure
pytest -k "test_mount"                  # run tests matching a pattern
```

## Code Formatting
```shell
ruff format .        # auto-format
ruff check --fix .   # lint and auto-fix
```

## Type Checking
```shell
mypy
```

## Documentation

Live-reloading doc server (opens in browser):
```shell
cd docs
sphinx-autobuild --open-browser --watch source source build
```

Or build once:
```shell
cd docs
make html
# open build/html/index.html
```

## Project Layout

```
responder/
├── responder/             # main package
│   ├── api.py             # API class — the entry point
│   ├── core.py            # public API surface (re-exports)
│   ├── routes.py          # Router, Route, WebSocketRoute, dependency injection
│   ├── models.py          # Request and Response wrappers
│   ├── params.py          # typed parameter markers (Query/Header/Cookie/Path)
│   ├── types.py           # public type aliases (Handler, Hook, Dependency)
│   ├── background.py      # background task queue
│   ├── formats.py         # content negotiation (JSON, YAML, msgpack)
│   ├── templates.py       # Jinja2 template rendering
│   ├── staticfiles.py     # static file serving
│   ├── status_codes.py    # HTTP status-code table
│   └── ext/               # extensions
│       ├── cli.py         # command-line interface
│       ├── sessions.py    # cookie & server-side sessions, backends
│       ├── logging.py     # request logging + request-id middleware
│       ├── metrics.py     # Prometheus-style metrics endpoint
│       ├── ratelimit.py   # rate limiting
│       ├── openapi/       # OpenAPI schema + interactive docs
│       └── graphql/       # GraphQL support
├── tests/                 # pytest test suite
├── examples/              # runnable example apps
├── docs/source/           # Sphinx documentation
└── pyproject.toml         # project metadata and tool config
```
