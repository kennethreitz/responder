"""
Tests for the application target loader in `responder.util.python`.

`_load_target_basic` is the fallback used when pueblo (the `cli` extra)
is not installed. It supports local modules and file paths only.
"""

import pytest

from responder.util.python import InvalidTarget, _load_target_basic, load_target


@pytest.fixture
def app_file(tmp_path):
    path = tmp_path / "app.py"
    path.write_text(
        "def api():\n    return 'default-instance'\n\n"
        "def service():\n    return 'named-instance'\n"
    )
    return path


def test_basic_loads_module_spec():
    obj = _load_target_basic("json:dumps", default_property="api")
    import json

    assert obj is json.dumps


def test_basic_loads_default_property_from_file(app_file):
    obj = _load_target_basic(str(app_file), default_property="api")
    assert obj() == "default-instance"


def test_basic_loads_named_property_from_file(app_file):
    obj = _load_target_basic(f"{app_file}:service", default_property="api")
    assert obj() == "named-instance"


def test_basic_rejects_url_with_install_hint():
    with pytest.raises(ImportError, match=r"responder\[cli\]"):
        _load_target_basic("https://example.com/app.py", default_property="api")


def test_basic_rejects_empty_spec():
    with pytest.raises(InvalidTarget):
        _load_target_basic(":api", default_property="api")


def test_basic_missing_property_raises_attribute_error(app_file):
    with pytest.raises(AttributeError):
        _load_target_basic(f"{app_file}:nope", default_property="api")


def test_load_target_file(app_file):
    """The public entry point loads file targets with or without pueblo."""
    obj = load_target(str(app_file))
    assert obj() == "default-instance"
