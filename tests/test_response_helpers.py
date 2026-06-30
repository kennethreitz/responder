import responder


def _api(**kwargs):
    return responder.API(
        title="T",
        version="1",
        secret_key="x" * 32,
        allowed_hosts=[";"],
        session_https_only=False,
        **kwargs,
    )


def test_response_created_sets_status_location_and_media():
    api = _api()

    @api.post("/items")
    def create_item(req, resp):
        resp.created({"id": 1, "name": "tea"}, location="/items/1")

    response = api.requests.post("/items")

    assert response.status_code == 201
    assert response.headers["location"] == "/items/1"
    assert response.json() == {"id": 1, "name": "tea"}


def test_response_no_content_clears_body_and_preserves_headers():
    api = _api()

    @api.delete("/items/1")
    def delete_item(req, resp):
        resp.media = {"will": "be cleared"}
        resp.no_content(headers={"X-Deleted": "1"})

    response = api.requests.delete("/items/1")

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["x-deleted"] == "1"


def test_response_problem_uses_problem_json_and_handler_enrichment():
    def problem_handler(payload, request, exc):
        payload["code"] = "item_conflict"
        return payload

    api = _api(problem_handler=problem_handler)

    @api.post("/items")
    def create_item(req, resp):
        resp.problem(
            409,
            "Item already exists",
            title="Conflict",
            type="https://example.com/problems/item-conflict",
            instance="/items",
            errors=[{"field": "name", "message": "duplicate"}],
            retryable=False,
        )

    response = api.requests.post("/items")

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://example.com/problems/item-conflict",
        "title": "Conflict",
        "status": 409,
        "detail": "Item already exists",
        "instance": "/items",
        "errors": [{"field": "name", "message": "duplicate"}],
        "code": "item_conflict",
        "retryable": False,
    }
