from pathlib import Path

import pytest

import responder


@pytest.fixture
def data_dir(current_dir):
    yield current_dir / "data"


@pytest.fixture()
def current_dir():
    yield Path(__file__).parent


@pytest.fixture
def api():
    return responder.API(debug=False, allowed_hosts=[";"])


@pytest.fixture
def session(api):
    return api.requests


@pytest.fixture
def url():
    def url_for(s):
        return f"http://;{s}"

    return url_for


@pytest.fixture
def flask():
    from flask import Flask

    app = Flask(__name__)

    @app.route("/")
    def hello():
        return "Hello World!"

    return app


@pytest.fixture
def template_path(tmpdir):
    # create a Jinja template file on the filesystem
    template_name = "test.html"
    template_file = tmpdir.mkdir("static").join(template_name)
    template_file.write("{{ var }}")
    return template_file


@pytest.fixture
def needs_openapi() -> None:
    try:
        import apispec

        _ = apispec.APISpec
    except ImportError as ex:
        raise pytest.skip("apispec package not installed") from ex
