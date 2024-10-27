(sandbox)=
# Development Sandbox

## Setup
Set up a development sandbox.

Acquire sources and create virtualenv.
```shell
git clone https://github.com/kennethreitz/responder
cd responder
python3 -m venv .venv
source .venv/bin/activate
```

Install project in editable mode.
```shell
pip install --editable '.[full,develop,docs,release,test]'
```

## Operations
Invoke linter and software tests.
```shell
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
