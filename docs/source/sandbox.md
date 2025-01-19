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
all runtime extensions and development tools.
```shell
uv pip install --upgrade --editable '.[full,develop,docs,release,test]'
```

## Operations
Invoke linter and software tests.
```shell
source .venv/bin/activate
poe check
```

Format code.
```shell
poe format
```

Documentation authoring.
```shell
poe docs-autobuild
```
