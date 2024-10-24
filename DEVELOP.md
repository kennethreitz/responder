# Development Sandbox

## Setup

Acquire sources and install project in editable mode.
```shell
git clone https://github.com/kennethreitz/responder
cd responder
python3 -m venv .venv
source .venv/bin/activate
pip install --editable '.[graphql,develop,release,test]'
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


## Release

```shell
git tag v2.1.0
git push --tags
poe release
```
