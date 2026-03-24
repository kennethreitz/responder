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
├── responder/          # main package
│   ├── api.py          # API class — the entry point
│   ├── routes.py       # Router, Route, WebSocketRoute
│   ├── models.py       # Request and Response wrappers
│   ├── ext/            # extensions (CLI, GraphQL, OpenAPI, rate limiting)
│   ├── background.py   # background task queue
│   └── formats.py      # content negotiation (JSON, YAML, msgpack)
├── tests/              # pytest test suite
├── examples/           # runnable example apps
├── docs/source/        # Sphinx documentation
└── pyproject.toml      # project metadata and tool config
```
