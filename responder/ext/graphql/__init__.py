import json
from functools import partial

from graphql_server import default_format_error, encode_execution_results, json_encode

from .templates import GRAPHIQL


class GraphQLView:
    def __init__(self, *, api, schema):
        self.api = api
        self.schema = schema

    @staticmethod
    async def _resolve_graphql_query(req, resp):
        # TODO: Get variables and operation_name from form data, params, request text?

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

        # Support query/q in form data.
        # Form data is awaiting https://github.com/encode/starlette/pull/102
        """
        if "query" in req.media("form"):
            return req.media("form")["query"], None, None
        if "q" in req.media("form"):
            return req.media("form")["q"], None, None
        """

        # Support query/q in params.
        if "query" in req.params:
            return req.params["query"], None, None
        if "q" in req.params:
            return req.params["q"], None, None

        # Otherwise, the request text is used (typical).
        # TODO: Make some assertions about content-type here.
        return req.text, None, None

    async def graphql_response(self, req, resp):
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
        result, status_code = encode_execution_results(
            [result],
            is_batch=False,
            format_error=default_format_error,
            encode=partial(json_encode, pretty=False),
        )
        resp.media = json.loads(result)
        return (query, result, status_code)

    async def on_request(self, req, resp):
        await self.graphql_response(req, resp)

    async def __call__(self, req, resp):
        await self.on_request(req, resp)
