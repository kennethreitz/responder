import json

from .templates import GRAPHIQL


class GraphQLView:
    """A class-based view that serves a GraphQL API.

    Handles query resolution from multiple sources (JSON body, query
    parameters, raw request text) and renders the GraphiQL IDE for
    browser requests.

    :param api: The Responder API instance.
    :param schema: A Graphene schema instance.
    """

    def __init__(self, *, api, schema):
        self.api = api
        self.schema = schema

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
        show_graphiql = req.method == "get" and req.accepts("text/html")

        if show_graphiql:
            resp.content = self.api.templates.render_string(
                GRAPHIQL, endpoint=req.url.path
            )
            return None

        query, variables, operation_name = await self._resolve_graphql_query(req, resp)
        if query is None:
            return None

        context = {"request": req, "response": resp}
        result = self.schema.execute(
            query, variables=variables, operation_name=operation_name, context=context
        )

        response_data = {}
        if result.errors:
            response_data["errors"] = [{"message": str(e)} for e in result.errors]
        if result.data is not None:
            response_data["data"] = result.data

        resp.media = response_data
        status_code = 200 if not result.errors else 400
        return (query, json.dumps(response_data), status_code)

    async def on_request(self, req, resp):
        await self.graphql_response(req, resp)

    async def __call__(self, req, resp):
        await self.on_request(req, resp)
