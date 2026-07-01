import json

from .templates import GRAPHIQL


class GraphQLView:
    """A class-based view that serves a GraphQL API.

    Handles query resolution from multiple sources (JSON body, query
    parameters, raw request text) and renders the GraphiQL IDE for
    browser requests.

    :param api: The Responder API instance.
    :param schema: A Graphene schema instance.
    :param graphiql: If ``True`` (default), serve the in-browser GraphiQL IDE
                     for HTML ``GET`` requests. Set ``False`` in production.
    :param introspection: If ``False``, reject schema-introspection queries
                          (``__schema``/``__type``). Defaults to ``True``.
    :param max_depth: Reject queries whose selection nesting exceeds this depth
                      (a DoS guard). ``None`` (default) means unlimited.
    """

    def __init__(
        self, *, api, schema, graphiql=True, introspection=True, max_depth=None
    ):
        self.api = api
        self.schema = schema
        self.graphiql = graphiql
        self.introspection = introspection
        self.max_depth = max_depth

    @staticmethod
    def _max_selection_depth(document):
        """The deepest nesting of the *executed* query (fragments expanded).

        Fragment spreads are followed into their definitions so an attacker
        can't evade the depth cap by chaining shallow fragments; only operation
        definitions are measured as roots, and cyclic spreads are broken.
        """
        from graphql.language import (
            FragmentDefinitionNode,
            FragmentSpreadNode,
            OperationDefinitionNode,
        )

        fragments = {
            defn.name.value: defn
            for defn in document.definitions
            if isinstance(defn, FragmentDefinitionNode)
        }

        def depth(node, seen):
            if isinstance(node, FragmentSpreadNode):
                name = node.name.value
                if name in seen or name not in fragments:
                    return 0
                return depth(fragments[name], seen | {name})
            selection_set = getattr(node, "selection_set", None)
            if selection_set is None:
                return 0
            return 1 + max(
                (depth(s, seen) for s in selection_set.selections), default=0
            )

        roots = [
            defn
            for defn in document.definitions
            if isinstance(defn, OperationDefinitionNode)
        ]
        return max((depth(r, frozenset()) for r in roots), default=0)

    def _validate_query(self, document):
        """Return a list of human-readable validation problems (empty if OK)."""
        from graphql import validate

        problems: list[str] = []
        if not self.introspection:
            from graphql.validation import NoSchemaIntrospectionCustomRule

            problems.extend(
                str(e)
                for e in validate(
                    self.schema.graphql_schema,
                    document,
                    [NoSchemaIntrospectionCustomRule],
                )
            )
        if self.max_depth:
            depth = self._max_selection_depth(document)
            if depth > self.max_depth:
                problems.append(
                    f"Query exceeds the maximum allowed depth of {self.max_depth} "
                    f"(got {depth})."
                )
        return problems

    @staticmethod
    def _selects_non_query(document, operation_name):
        """Whether the operation GraphQL would execute is a mutation/subscription.

        Per the GraphQL-over-HTTP spec, ``GET`` may only run ``query``
        operations: allowing mutations over ``GET`` makes them CSRF-able (a
        simple ``<img src>`` triggers them with the victim's cookies) and
        cacheable.
        """
        from graphql.language import OperationDefinitionNode, OperationType

        operations = [
            defn
            for defn in document.definitions
            if isinstance(defn, OperationDefinitionNode)
        ]
        if operation_name:
            operations = [
                op for op in operations if op.name and op.name.value == operation_name
            ]
        return any(op.operation is not OperationType.QUERY for op in operations)

    @staticmethod
    def _parse_variables(raw):
        """Parse variables from a string (query param) or return as-is (dict)."""
        if raw is None:
            return None
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return None
        return raw

    @staticmethod
    async def _resolve_graphql_query(req, resp):
        """Extract query, variables, and operationName from the request.

        Supports multiple input sources, checked in order:

        1. JSON body (``Content-Type: application/json``)
        2. Form data (``Content-Type: application/x-www-form-urlencoded``)
        3. Query parameters (``?query=...&variables=...&operationName=...``)
        4. Raw request text
        """
        if "json" in req.mimetype:
            json_media = await req.media("json")
            if "query" not in json_media:
                resp.status_code = 400
                resp.media = {"errors": ["'query' key is required in the JSON payload"]}
                return None, None, None
            return (
                json_media["query"],
                json_media.get("variables"),
                json_media.get("operationName"),
            )

        if "form" in req.mimetype:
            form_data = await req.media("form")
            if "query" in form_data:
                return (
                    form_data["query"],
                    GraphQLView._parse_variables(form_data.get("variables")),
                    form_data.get("operationName"),
                )

        # Support query/variables/operationName in query params.
        if "query" in req.params:
            return (
                req.params["query"],
                GraphQLView._parse_variables(req.params.get("variables")),
                req.params.get("operationName"),
            )
        if "q" in req.params:
            return req.params["q"], None, None

        # Otherwise, the request text is used (typical).
        return await req.text, None, None

    async def graphql_response(self, req, resp):
        """Process a GraphQL request and populate the response."""
        show_graphiql = (
            self.graphiql and req.method == "GET" and req.accepts("text/html")
        )

        if show_graphiql:
            resp.content = self.api.templates.render_string(
                GRAPHIQL, endpoint=req.url.path
            )
            return

        query, variables, operation_name = await self._resolve_graphql_query(req, resp)
        if query is None:
            return

        # Parse once; the document is shared by the GET guard and validation.
        # A syntax error leaves it None so normal execution can surface it.
        from graphql import parse

        try:
            document = parse(query)
        except Exception:
            document = None

        # GET must be safe/idempotent: only allow query operations, never
        # mutations (which would otherwise be CSRF-able and cacheable via GET).
        if (
            document is not None
            and req.method == "GET"
            and self._selects_non_query(document, operation_name)
        ):
            resp.status_code = 405
            resp.headers["Allow"] = "POST"
            resp.media = {
                "errors": [
                    {"message": "Mutations must use POST, not GET."}
                ]
            }
            return

        if document is not None and (not self.introspection or self.max_depth):
            problems = self._validate_query(document)
            if problems:
                resp.media = {"errors": [{"message": m} for m in problems]}
                resp.status_code = 400
                return

        # execute_async awaits ``async def`` resolvers (first-class in
        # graphene 3) instead of leaving them as never-awaited coroutines,
        # and lets them yield the event loop while they wait.
        context = {"request": req, "response": resp}
        result = await self.schema.execute_async(
            query, variables=variables, operation_name=operation_name, context=context
        )

        response_data = {}
        if result.errors:
            response_data["errors"] = [{"message": str(e)} for e in result.errors]
        if result.data is not None:
            response_data["data"] = result.data

        resp.media = response_data
        resp.status_code = 200 if not result.errors else 400

    async def on_request(self, req, resp):
        await self.graphql_response(req, resp)

    async def __call__(self, req, resp):
        await self.on_request(req, resp)
