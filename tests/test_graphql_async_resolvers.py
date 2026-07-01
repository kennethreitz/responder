# ruff: noqa: E402
"""GraphQL execution is async-native: ``async def`` resolvers work, and
resolvers run via ``schema.execute_async`` so a slow resolver doesn't need
to block the event loop with a sync ``schema.execute`` call.
"""

import asyncio

import pytest

graphene = pytest.importorskip("graphene")

from responder.ext.graphql import GraphQLView


@pytest.fixture
def async_schema():
    class Query(graphene.ObjectType):
        hello = graphene.String(name=graphene.String(default_value="stranger"))

        async def resolve_hello(self, info, name):
            await asyncio.sleep(0)
            return f"Hello {name}"

    return graphene.Schema(query=Query)


@pytest.fixture
def mixed_schema():
    class Query(graphene.ObjectType):
        sync_field = graphene.String()
        async_field = graphene.String()

        def resolve_sync_field(self, info):
            return "sync"

        async def resolve_async_field(self, info):
            await asyncio.sleep(0)
            return "async"

    return graphene.Schema(query=Query)


def test_async_resolver_over_post(api, async_schema):
    api.add_route("/", GraphQLView(schema=async_schema, api=api))
    r = api.requests.post("http://;/", json={"query": "{ hello }"})
    assert r.status_code == 200
    assert r.json() == {"data": {"hello": "Hello stranger"}}


def test_async_resolver_over_get(api, async_schema):
    api.add_route("/", GraphQLView(schema=async_schema, api=api))
    r = api.requests.get("http://;/?query={ hello }", headers={"Accept": "json"})
    assert r.status_code == 200
    assert r.json() == {"data": {"hello": "Hello stranger"}}


def test_async_resolver_with_variables(api, async_schema):
    api.add_route("/", GraphQLView(schema=async_schema, api=api))
    r = api.requests.post(
        "http://;/",
        json={
            "query": "query H($name: String) { hello(name: $name) }",
            "variables": {"name": "world"},
        },
    )
    assert r.status_code == 200
    assert r.json() == {"data": {"hello": "Hello world"}}


def test_mixed_sync_and_async_resolvers(api, mixed_schema):
    api.add_route("/", GraphQLView(schema=mixed_schema, api=api))
    r = api.requests.post("http://;/", json={"query": "{ syncField asyncField }"})
    assert r.status_code == 200
    assert r.json() == {"data": {"syncField": "sync", "asyncField": "async"}}


def test_sync_resolver_still_works(api):
    class Query(graphene.ObjectType):
        hello = graphene.String()

        def resolve_hello(self, info):
            return "hi"

    api.add_route("/", GraphQLView(schema=graphene.Schema(query=Query), api=api))
    r = api.requests.post("http://;/", json={"query": "{ hello }"})
    assert r.status_code == 200
    assert r.json() == {"data": {"hello": "hi"}}


def test_syntax_error_still_returns_400(api, async_schema):
    api.add_route("/", GraphQLView(schema=async_schema, api=api))
    r = api.requests.post("http://;/", json={"query": "{ hello"})
    assert r.status_code == 400
    assert "errors" in r.json()


def test_async_mutation(api):
    class Query(graphene.ObjectType):
        hello = graphene.String()

        def resolve_hello(self, info):
            return "hi"

    class CreateUser(graphene.Mutation):
        class Arguments:
            name = graphene.String(required=True)

        ok = graphene.Boolean()

        async def mutate(self, info, name):
            await asyncio.sleep(0)
            return CreateUser(ok=True)

    class Mutation(graphene.ObjectType):
        create_user = CreateUser.Field()

    schema = graphene.Schema(query=Query, mutation=Mutation)
    api.add_route("/", GraphQLView(schema=schema, api=api))
    r = api.requests.post(
        "http://;/", json={"query": 'mutation { createUser(name: "eve") { ok } }'}
    )
    assert r.status_code == 200
    assert r.json() == {"data": {"createUser": {"ok": True}}}
