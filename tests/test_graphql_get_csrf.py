# ruff: noqa: E402
"""GET requests may only run GraphQL query operations, never mutations.

Allowing a mutation over GET makes it CSRF-able (a plain ``<img src>`` would
trigger it with the victim's cookies) and cacheable. Mutations must use POST.
"""

import pytest

graphene = pytest.importorskip("graphene")

from responder.ext.graphql import GraphQLView


@pytest.fixture
def mutation_schema():
    class Query(graphene.ObjectType):
        hello = graphene.String()

        def resolve_hello(self, info):
            return "hi"

    class CreateUser(graphene.Mutation):
        class Arguments:
            name = graphene.String(required=True)

        ok = graphene.Boolean()

        def mutate(self, info, name):
            return CreateUser(ok=True)

    class Mutation(graphene.ObjectType):
        create_user = CreateUser.Field()

    return graphene.Schema(query=Query, mutation=Mutation)


def test_mutation_over_get_is_rejected(api, mutation_schema):
    api.add_route("/", GraphQLView(schema=mutation_schema, api=api))
    r = api.requests.get(
        'http://;/?query=mutation { createUser(name: "eve") { ok } }',
        headers={"Accept": "json"},
    )
    assert r.status_code == 405
    assert r.headers["Allow"] == "POST"
    assert "POST" in r.json()["errors"][0]["message"]


def test_query_over_get_still_works(api, mutation_schema):
    api.add_route("/", GraphQLView(schema=mutation_schema, api=api))
    r = api.requests.get("http://;/?query={ hello }", headers={"Accept": "json"})
    assert r.status_code == 200
    assert r.json() == {"data": {"hello": "hi"}}


def test_mutation_over_post_still_works(api, mutation_schema):
    api.add_route("/", GraphQLView(schema=mutation_schema, api=api))
    r = api.requests.post(
        "http://;/", json={"query": 'mutation { createUser(name: "eve") { ok } }'}
    )
    assert r.status_code == 200
    assert r.json() == {"data": {"createUser": {"ok": True}}}


def test_named_mutation_over_get_is_rejected(api, mutation_schema):
    # A document with a named mutation selected by operationName is also blocked.
    api.add_route("/", GraphQLView(schema=mutation_schema, api=api))
    doc = 'mutation M { createUser(name: "eve") { ok } }'
    r = api.requests.get(
        f"http://;/?query={doc}&operationName=M", headers={"Accept": "json"}
    )
    assert r.status_code == 405
