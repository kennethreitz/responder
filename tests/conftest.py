import graphene
import responder
from pathlib import Path
import pytest
import multiprocessing
import concurrent.futures


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
def reverse_proxied_api():
    return responder.API(debug=False, allowed_hosts=[";"], reverse_proxy_route="/demo")


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
def schema():
    class Query(graphene.ObjectType):
        hello = graphene.String(name=graphene.String(default_value="stranger"))

        def resolve_hello(self, info, name):
            return f"Hello {name}"

    return graphene.Schema(query=Query)


@pytest.fixture
def template_path(tmpdir):
    # create a Jinja template file on the filesystem
    template_name = "test.html"
    template_file = tmpdir.mkdir("static").join(template_name)
    template_file.write("{{ var }}")
    return template_file
