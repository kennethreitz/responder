import json

from .templates import GRAPHIQL


class GraphQLView:
    def __init__(self, *, api, schema):
        self.api = api
        self.schema = schema

    @staticmethod
    async def _resolve_graphql_query(req, resp):
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

        # Support query/q in params.
        if "query" in req.params:
            return req.params["query"], None, None
        if "q" in req.params:
            return req.params["q"], None, None

        # Otherwise, the request text is used (typical).
        return await req.text, None, None

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
