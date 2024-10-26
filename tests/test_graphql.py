# ruff: noqa: E402
import pytest

graphene = pytest.importorskip("graphene")

from responder.ext.graphql import GraphQLView


@pytest.fixture
def schema():
    class Query(graphene.ObjectType):
        hello = graphene.String(name=graphene.String(default_value="stranger"))

        def resolve_hello(self, info, name):
            return f"Hello {name}"

    return graphene.Schema(query=Query)


def test_graphql_schema_query_querying(api, schema):
    api.add_route("/", GraphQLView(schema=schema, api=api))
    r = api.requests.get("http://;/?q={ hello }", headers={"Accept": "json"})
    assert r.status_code == 200
    assert r.json() == {"data": {"hello": "Hello stranger"}}


def test_graphql_schema_json_query(api, schema):
    api.add_route("/", GraphQLView(schema=schema, api=api))
    r = api.requests.post("http://;/", json={"query": "{ hello }"})
    assert r.status_code < 300
    assert r.json() == {"data": {"hello": "Hello stranger"}}


def test_graphiql(api, schema):
    api.add_route("/", GraphQLView(schema=schema, api=api))
    r = api.requests.get("http://;/", headers={"Accept": "text/html"})
    assert r.status_code < 300
    assert "GraphiQL" in r.text
