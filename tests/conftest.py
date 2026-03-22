import pytest

import responder


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
def template_path(tmp_path):
    template_dir = tmp_path / "static"
    template_dir.mkdir()
    template_file = template_dir / "test.html"
    template_file.write_text("{{ var }}")
    return template_file


@pytest.fixture
def needs_openapi() -> None:
    try:
        import apispec

        _ = apispec.APISpec
    except ImportError as ex:
        raise pytest.skip("apispec package not installed") from ex
