(sandbox)=
# Development Sandbox

## Setup
Set up a development sandbox.

Acquire sources and create virtualenv.
```shell
git clone https://github.com/kennethreitz/responder.git
cd responder
uv venv
```

Install project in editable mode, including
all development tools.
```shell
uv pip install --upgrade --editable '.[develop,docs,release,test]'
```

## Operations
Run tests.
```shell
source .venv/bin/activate
pytest
```

Format code.
```shell
ruff format .
ruff check --fix .
```

Documentation authoring.
```shell
sphinx-autobuild --open-browser --watch docs/source docs/source docs/build
```
