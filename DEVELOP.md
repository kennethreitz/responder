# Development Sandbox

Set up a development sandbox.

Acquire sources and install project in editable mode.
```shell
git clone https://github.com/kennethreitz/responder
cd responder
python3 -m venv .venv
source .venv/bin/activate
pip install --editable '.[graphql,develop,docs,release,test]'
```

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
