import yaml
import json


def format_form(r, encode=False):
    return r._wz.form


def format_yaml(r, encode=False):
    if encode:
        return yaml.load(r.content)
    else:
        return yaml.dump(r.media)


def format_json(r, encode=False):
    if encode:
        return json.loads(r.content)
    else:
        return json.dumps(r.media)


def get_formats():
    return {"json": format_json, "yaml": format_yaml, "form": format_form}
