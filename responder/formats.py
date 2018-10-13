import yaml
import json


async def format_form(r, encode=False):
    if not encode:
        return await r._starlette.form()


def format_yaml(r, encode=False):
    if encode:
        r.headers.update({"Content-Type": "application/x-yaml"})
        return yaml.dump(r.media)
    else:
        return yaml.safe_load(r.content)


def format_json(r, encode=False):
    if encode:
        r.headers.update({"Content-Type": "application/json"})
        return json.dumps(r.media)
    else:
        return json.loads(r.content)


def get_formats():
    return {"json": format_json, "yaml": format_yaml, "form": format_form}
