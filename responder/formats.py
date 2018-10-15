import yaml
import json


async def format_form(r, encode=False):
    if not encode:
        return await r._starlette.form()


async def format_yaml(r, encode=False):
    if encode:
        r.headers.update({"Content-Type": "application/x-yaml"})
        return yaml.safe_dump(r.media)
    else:
        return yaml.safe_load(await r.content)


async def format_json(r, encode=False):
    if encode:
        r.headers.update({"Content-Type": "application/json"})
        return json.dumps(r.media)
    else:
        return json.loads(await r.content)


def get_formats():
    return {"json": format_json, "yaml": format_yaml, "form": format_form}
