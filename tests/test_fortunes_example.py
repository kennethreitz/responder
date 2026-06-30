import subprocess

import yaml
from openapi_spec_validator import validate

from examples.fortunes import FortuneUnavailable, create_api, read_fortune


def test_fortunes_example_openapi_contract():
    api = create_api(fortune_reader=lambda: "You will test the contract.")
    spec = yaml.safe_load(api.requests.get("/schema.yml").content)

    validate(spec)
    operation = spec["paths"]["/fortune"]["get"]
    assert operation["operationId"] == "get_fortune"
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/FortuneOut"
    }
    assert operation["responses"]["503"]["description"] == (
        "Fortune command unavailable"
    )


def test_fortunes_example_returns_a_fortune():
    api = create_api(
        fortune_reader=lambda: "A fresh example is its own tiny lantern.",
        command="fortune",
    )

    response = api.requests.get("/fortune")

    assert response.status_code == 200
    assert response.json() == {
        "fortune": "A fresh example is its own tiny lantern.",
        "source": "fortune",
    }


def test_fortunes_example_reports_missing_cli():
    def missing_fortune():
        raise FortuneUnavailable("Install 'fortune' before reading fortunes.")

    api = create_api(fortune_reader=missing_fortune, command="fortune")
    response = api.requests.get("/fortune")

    assert response.status_code == 503
    assert response.json()["command"] == "fortune"
    assert response.json()["type"].endswith("/fortune-unavailable")


def test_read_fortune_strips_stdout(monkeypatch):
    def run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=" wise words\n\n")

    monkeypatch.setattr("examples.fortunes.shutil.which", lambda command: command)
    monkeypatch.setattr("examples.fortunes.subprocess.run", run)

    assert read_fortune("fortune") == "wise words"
