"""YAML responses use RFC 9512 ``application/yaml``; empty YAML bodies are 400."""

def _media_api(api):
    @api.route("/", methods=["POST"])
    async def view(req, resp):
        resp.media = {"got": await req.media("yaml")}

    return api


def test_yaml_response_content_type_is_application_yaml(api):
    @api.route("/")
    def view(req, resp):
        resp.media = {"key": "value"}

    r = api.requests.get("/", headers={"Accept": "application/yaml"})
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/yaml"
    assert "key: value" in r.text


def test_legacy_x_yaml_accept_still_negotiates(api):
    """Clients sending the legacy Accept header still get YAML back."""

    @api.route("/")
    def view(req, resp):
        resp.media = {"key": "value"}

    r = api.requests.get("/", headers={"Accept": "application/x-yaml"})
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/yaml"
    assert "key: value" in r.text


def test_inbound_application_yaml_body_parses(api):
    _media_api(api)
    r = api.requests.post(
        "/", content=b"key: value\n", headers={"Content-Type": "application/yaml"}
    )
    assert r.status_code == 200
    assert r.json() == {"got": {"key": "value"}}


def test_inbound_legacy_x_yaml_body_parses(api):
    _media_api(api)
    r = api.requests.post(
        "/", content=b"key: value\n", headers={"Content-Type": "application/x-yaml"}
    )
    assert r.status_code == 200
    assert r.json() == {"got": {"key": "value"}}


def test_empty_yaml_body_is_400(api):
    """Matches the JSON format: an empty body is a client error, not None."""
    _media_api(api)
    r = api.requests.post("/", content=b"", headers={"Content-Type": "application/yaml"})
    assert r.status_code == 400


def test_whitespace_only_yaml_body_is_400(api):
    _media_api(api)
    r = api.requests.post(
        "/", content=b"  \n\t\n", headers={"Content-Type": "application/yaml"}
    )
    assert r.status_code == 400


def test_explicit_yaml_null_body_is_not_400(api):
    """An explicit YAML null document is valid — only *empty* bodies 400."""
    _media_api(api)
    r = api.requests.post(
        "/", content=b"null\n", headers={"Content-Type": "application/yaml"}
    )
    assert r.status_code == 200
    assert r.json() == {"got": None}


def test_malformed_yaml_body_still_400(api):
    _media_api(api)
    r = api.requests.post(
        "/",
        content=b"key: : : bad\n  - x",
        headers={"Content-Type": "application/yaml"},
    )
    assert r.status_code == 400


def test_openapi_schema_served_as_application_yaml(api, needs_openapi):
    from responder.ext.openapi import OpenAPISchema

    OpenAPISchema(app=api, title="Test", version="1.0", openapi="3.0.2")

    r = api.requests.get("/schema.yml")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/yaml"
