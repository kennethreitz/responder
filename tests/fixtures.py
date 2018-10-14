import graphene
import responder
import pytest


@pytest.fixture
def api():
    return responder.API()


@pytest.fixture
def session(api):
    return api.session()


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
