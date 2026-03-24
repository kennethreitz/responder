# ruff: noqa: E402
import json

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


@pytest.fixture
def mutation_schema():
    class Query(graphene.ObjectType):
        hello = graphene.String(name=graphene.String(default_value="stranger"))

        def resolve_hello(self, info, name):
            return f"Hello {name}"

    class CreateUser(graphene.Mutation):
        class Arguments:
            name = graphene.String(required=True)

        ok = graphene.Boolean()
        name = graphene.String()

        def mutate(self, info, name):
            return CreateUser(ok=True, name=name)

    class Mutation(graphene.ObjectType):
        create_user = CreateUser.Field()

    return graphene.Schema(query=Query, mutation=Mutation)


@pytest.fixture
def multi_op_schema():
    class Query(graphene.ObjectType):
        hello = graphene.String(name=graphene.String(default_value="stranger"))
        goodbye = graphene.String(name=graphene.String(default_value="stranger"))

        def resolve_hello(self, info, name):
            return f"Hello {name}"

        def resolve_goodbye(self, info, name):
            return f"Goodbye {name}"

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


def test_graphql_shorthand(api, schema):
    """Test the api.graphql() shorthand method."""
    api.graphql("/gql", schema=schema)
    r = api.requests.post("http://;/gql", json={"query": "{ hello }"})
    assert r.status_code < 300
    assert r.json() == {"data": {"hello": "Hello stranger"}}


def test_graphql_missing_query_key(api, schema):
    api.add_route("/", GraphQLView(schema=schema, api=api))
    r = api.requests.post("http://;/", json={"not_query": "foo"})
    assert r.status_code == 400
    assert "errors" in r.json()


def test_graphql_query_param(api, schema):
    api.add_route("/", GraphQLView(schema=schema, api=api))
    r = api.requests.get("http://;/?query={ hello }", headers={"Accept": "json"})
    assert r.json() == {"data": {"hello": "Hello stranger"}}


def test_graphql_error_response(api, schema):
    api.add_route("/", GraphQLView(schema=schema, api=api))
    r = api.requests.post("http://;/", json={"query": "{ nonexistent }"})
    assert "errors" in r.json()


def test_graphql_variables_json(api, schema):
    """Variables passed via JSON body."""
    api.add_route("/", GraphQLView(schema=schema, api=api))
    r = api.requests.post(
        "http://;/",
        json={
            "query": "query Hello($name: String!) { hello(name: $name) }",
            "variables": {"name": "Alice"},
        },
    )
    assert r.json() == {"data": {"hello": "Hello Alice"}}


def test_graphql_variables_query_param(api, schema):
    """Variables passed as JSON string in query parameter."""
    api.add_route("/", GraphQLView(schema=schema, api=api))
    variables = json.dumps({"name": "Bob"})
    r = api.requests.get(
        f"http://;/?query=query Hello($name: String!) "
        f"{{ hello(name: $name) }}&variables={variables}",
        headers={"Accept": "json"},
    )
    assert r.json() == {"data": {"hello": "Hello Bob"}}


def test_graphql_operation_name_json(api, multi_op_schema):
    """operationName selects which operation to run."""
    api.add_route("/", GraphQLView(schema=multi_op_schema, api=api))
    query = """
        query SayHello { hello }
        query SayGoodbye { goodbye }
    """
    r = api.requests.post(
        "http://;/",
        json={
            "query": query,
            "operationName": "SayHello",
        },
    )
    data = r.json()
    assert data["data"]["hello"] == "Hello stranger"


def test_graphql_operation_name_query_param(api, multi_op_schema):
    """operationName via query parameter."""
    api.add_route("/", GraphQLView(schema=multi_op_schema, api=api))
    query = "query SayHello { hello } query SayGoodbye { goodbye }"
    r = api.requests.get(
        f"http://;/?query={query}&operationName=SayGoodbye",
        headers={"Accept": "json"},
    )
    data = r.json()
    assert data["data"]["goodbye"] == "Goodbye stranger"


def test_graphql_mutation(api, mutation_schema):
    """Mutations work via JSON body."""
    api.add_route("/", GraphQLView(schema=mutation_schema, api=api))
    r = api.requests.post(
        "http://;/",
        json={
            "query": 'mutation { createUser(name: "Eve") { ok name } }',
        },
    )
    data = r.json()
    assert data["data"]["createUser"]["ok"] is True
    assert data["data"]["createUser"]["name"] == "Eve"


def test_graphql_mutation_with_variables(api, mutation_schema):
    """Mutations with variables."""
    api.add_route("/", GraphQLView(schema=mutation_schema, api=api))
    r = api.requests.post(
        "http://;/",
        json={
            "query": "mutation CreateUser($name: String!) "
            "{ createUser(name: $name) { ok name } }",
            "variables": {"name": "Frank"},
        },
    )
    data = r.json()
    assert data["data"]["createUser"]["ok"] is True
    assert data["data"]["createUser"]["name"] == "Frank"


def test_graphql_context_access(api):
    """Resolvers can access request and response via info.context."""

    class Query(graphene.ObjectType):
        method = graphene.String()

        def resolve_method(self, info):
            return info.context["request"].method

    schema = graphene.Schema(query=Query)
    api.add_route("/", GraphQLView(schema=schema, api=api))
    r = api.requests.post("http://;/", json={"query": "{ method }"})
    assert r.json() == {"data": {"method": "post"}}


def test_graphql_malformed_query(api, schema):
    """Malformed GraphQL syntax returns errors."""
    api.add_route("/", GraphQLView(schema=schema, api=api))
    r = api.requests.post("http://;/", json={"query": "{ this is not valid"})
    data = r.json()
    assert "errors" in data
    assert len(data["errors"]) > 0


def test_graphql_raw_text_query(api, schema):
    """Query sent as raw text body."""
    api.add_route("/", GraphQLView(schema=schema, api=api))
    r = api.requests.post(
        "http://;/",
        content=b"{ hello }",
        headers={"Content-Type": "text/plain"},
    )
    assert r.json() == {"data": {"hello": "Hello stranger"}}


def test_graphql_invalid_variables_query_param(api, schema):
    """Invalid JSON in variables query param is treated as None."""
    api.add_route("/", GraphQLView(schema=schema, api=api))
    r = api.requests.get(
        "http://;/?query={ hello }&variables=not-json",
        headers={"Accept": "json"},
    )
    assert r.json() == {"data": {"hello": "Hello stranger"}}
