"""8.1.0 OpenAPI feature tests.

Covers: the ``status_code=`` route option (63) — the default success status is
pre-seeded on the response and documented in the OpenAPI operation instead of
the hardcoded ``200`` — and caching of the generated OpenAPI document with
invalidation when routes, schemas, or security schemes change (67).
"""

import enum

import pytest
import yaml
from openapi_spec_validator import validate
from pydantic import BaseModel, ConfigDict

import responder
from responder import File, Form, Router, UploadFile


def _api(**kwargs):
    kwargs.setdefault("openapi", "3.1.0")
    return responder.API(
        title="T",
        version="1",
        allowed_hosts=[";"],
        session_https_only=False,
        **kwargs,
    )


def _spec_of(api):
    return yaml.safe_load(api.openapi.openapi)


class ItemIn(BaseModel):
    name: str


class ItemOut(BaseModel):
    id: int
    name: str


# ---------------------------------------------------------------------------
# Item 63: status_code= pre-seeds the response's success status.
# ---------------------------------------------------------------------------


def test_status_code_seeds_default_success_status():
    api = _api()

    @api.route("/items", methods=["POST"], status_code=201)
    async def create_item(req, resp):
        resp.media = {"id": 1}

    r = api.requests.post("http://;/items")
    assert r.status_code == 201
    assert r.json() == {"id": 1}


def test_status_code_explicit_assignment_in_view_wins():
    api = _api()

    @api.route("/items", methods=["POST"], status_code=201)
    async def create_item(req, resp):
        resp.status_code = 202
        resp.media = {"queued": True}

    r = api.requests.post("http://;/items")
    assert r.status_code == 202


def test_status_code_default_is_still_200_without_option():
    api = _api()

    @api.route("/plain")
    def plain(req, resp):
        resp.media = {"ok": True}

    r = api.requests.get("http://;/plain")
    assert r.status_code == 200


def test_status_code_on_method_sugar_and_router_groups():
    api = _api()

    @api.post("/sugar", status_code=201)
    def sugar(req, resp):
        resp.media = {"ok": True}

    group = api.group("/v1")

    @group.post("/grouped", status_code=202)
    def grouped(req, resp):
        resp.media = {"ok": True}

    assert api.requests.post("http://;/sugar").status_code == 201
    assert api.requests.post("http://;/v1/grouped").status_code == 202


def test_status_code_on_class_based_view():
    api = _api()

    @api.route("/cbv", status_code=201)
    class Items:
        def on_post(self, req, resp):
            resp.media = {"id": 1}

    r = api.requests.post("http://;/cbv")
    assert r.status_code == 201


def test_status_code_hook_short_circuit_still_wins():
    api = _api()

    def deny(req, resp):
        resp.status_code = 403
        resp.media = {"detail": "nope"}

    @api.route("/guarded", methods=["POST"], status_code=201, before=[deny])
    def guarded(req, resp):  # pragma: no cover - skipped by the hook
        resp.media = {"id": 1}

    r = api.requests.post("http://;/guarded")
    assert r.status_code == 403


def test_status_code_validation_error_still_422():
    api = _api()

    @api.route("/items", methods=["POST"], status_code=201)
    async def create_item(req, resp, *, item: ItemIn):
        resp.media = {"id": 1, "name": item.name}

    ok = api.requests.post("http://;/items", json={"name": "thing"})
    assert ok.status_code == 201
    bad = api.requests.post("http://;/items", json={"name": 5.5})
    assert bad.status_code == 422


def test_status_code_rejects_invalid_codes():
    api = _api()
    with pytest.raises(ValueError):
        api.route("/bad", status_code=99)
    with pytest.raises(ValueError):
        api.route("/bad", status_code=1000)


# ---------------------------------------------------------------------------
# Item 63: status_code= keys the documented success response.
# ---------------------------------------------------------------------------


def test_status_code_documented_instead_of_200():
    api = _api()

    @api.route(
        "/items", methods=["POST"], status_code=201, response_model=ItemOut
    )
    async def create_item(req, resp, *, item: ItemIn):
        resp.media = {"id": 1, "name": item.name}

    spec = _spec_of(api)
    responses = spec["paths"]["/items"]["post"]["responses"]
    assert "200" not in responses
    assert responses["201"]["description"] == "Successful response"
    schema = responses["201"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/ItemOut"}
    # Framework error responses are unaffected.
    assert "422" in responses
    validate(spec)


def test_status_code_204_documents_no_body():
    api = _api()

    @api.route("/items/{id:int}", methods=["DELETE"], status_code=204)
    async def delete_item(req, resp, *, id):
        pass

    r = api.requests.delete("http://;/items/3")
    assert r.status_code == 204
    assert r.content == b""

    spec = _spec_of(api)
    responses = spec["paths"]["/items/{id}"]["delete"]["responses"]
    assert "200" not in responses
    assert "content" not in responses["204"]
    validate(spec)


def test_status_code_examples_attach_to_declared_status():
    api = _api()

    @api.post(
        "/items",
        status_code=201,
        examples={"made": {"value": {"id": 1, "name": "thing"}}},
    )
    def create_item(req, resp):
        resp.media = {"id": 1, "name": "thing"}

    spec = _spec_of(api)
    responses = spec["paths"]["/items"]["post"]["responses"]
    examples = responses["201"]["content"]["application/json"]["examples"]
    assert examples["made"]["value"] == {"id": 1, "name": "thing"}
    assert "200" not in responses
    validate(spec)


def test_status_code_on_class_based_view_documented():
    api = _api()

    @api.route("/cbv", status_code=201)
    class Items:
        def on_post(self, req, resp):
            resp.media = {"id": 1}

    spec = _spec_of(api)
    responses = spec["paths"]["/cbv"]["post"]["responses"]
    assert "201" in responses
    assert "200" not in responses


def test_include_router_passes_status_code_through():
    router = Router(prefix="/items")

    @router.post("", status_code=201)
    def create_item(req, resp):
        resp.media = {"id": 1}

    api = _api()
    api.include_router(router, prefix="/v1")

    r = api.requests.post("http://;/v1/items")
    assert r.status_code == 201

    spec = _spec_of(api)
    responses = spec["paths"]["/v1/items"]["post"]["responses"]
    assert "201" in responses
    assert "200" not in responses


# ---------------------------------------------------------------------------
# Item 67: the generated OpenAPI document is cached...
# ---------------------------------------------------------------------------


def test_apispec_is_cached_between_accesses():
    api = _api()

    @api.route("/one")
    def one(req, resp):
        resp.media = {"n": 1}

    assert api.openapi._apispec is api.openapi._apispec
    # The rendered YAML is cached too (immutable, so identity is safe).
    assert api.openapi.openapi is api.openapi.openapi


def test_schema_response_serves_cached_document():
    api = _api()

    @api.route("/one")
    def one(req, resp):
        resp.media = {"n": 1}

    first = api.requests.get("http://;/schema.yml")
    assert first.status_code == 200
    cached = api.openapi._spec_cache
    assert cached is not None
    second = api.requests.get("http://;/schema.yml")
    assert second.text == first.text
    assert api.openapi._spec_cache is cached  # not rebuilt between requests

    # JSON negotiation is served from the same cached spec.
    as_json = api.requests.get(
        "http://;/schema.yml", headers={"Accept": "application/json"}
    )
    assert as_json.json()["paths"] == yaml.safe_load(first.text)["paths"]
    assert api.openapi._spec_cache is cached


# ---------------------------------------------------------------------------
# ... and invalidated when routes, schemas, or security schemes change.
# ---------------------------------------------------------------------------


def test_adding_route_after_first_render_regenerates():
    api = _api()

    @api.route("/early")
    def early(req, resp):
        resp.media = {"n": 1}

    before = yaml.safe_load(api.requests.get("http://;/schema.yml").text)
    assert "/later" not in before["paths"]

    @api.route("/later")
    def later(req, resp):
        resp.media = {"n": 2}

    after = yaml.safe_load(api.requests.get("http://;/schema.yml").text)
    assert "/later" in after["paths"]


def test_adding_schema_after_first_render_regenerates():
    api = _api()

    @api.route("/early")
    def early(req, resp):
        resp.media = {"n": 1}

    before = _spec_of(api)
    assert "Registered" not in before.get("components", {}).get("schemas", {})

    class Registered(BaseModel):
        name: str

    api.schema("Registered")(Registered)

    after = _spec_of(api)
    assert "Registered" in after["components"]["schemas"]


def test_adding_security_scheme_after_first_render_regenerates():
    api = _api()

    @api.route("/early")
    def early(req, resp):
        resp.media = {"n": 1}

    before = _spec_of(api)
    assert "bearer" not in before.get("components", {}).get("securitySchemes", {})

    api.add_security_scheme("bearer", {"type": "http", "scheme": "bearer"})

    after = _spec_of(api)
    assert after["components"]["securitySchemes"]["bearer"] == {
        "type": "http",
        "scheme": "bearer",
    }


def test_replacing_schema_same_name_regenerates():
    api = _api()

    class Thing(BaseModel):
        name: str

    api.openapi.add_schema("Thing", Thing)
    before = _spec_of(api)
    assert "name" in before["components"]["schemas"]["Thing"]["properties"]

    class OtherThing(BaseModel):
        label: str

    api.openapi.add_schema("Thing", OtherThing, check_existing=False)
    after = _spec_of(api)
    assert "label" in after["components"]["schemas"]["Thing"]["properties"]


# ---------------------------------------------------------------------------
# Pydantic Form-model binding is documented as the form requestBody schema.
# ---------------------------------------------------------------------------


class ProfileForm(BaseModel):
    name: str
    age: int = 0


def _collect_refs(obj, acc):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "$ref" and isinstance(value, str):
                acc.append(value)
            else:
                _collect_refs(value, acc)
    elif isinstance(obj, list):
        for item in obj:
            _collect_refs(item, acc)
    return acc


@pytest.mark.parametrize("openapi_version", ["3.0.3", "3.1.0"])
def test_form_model_documents_urlencoded_request_body(openapi_version):
    api = _api(openapi=openapi_version)

    @api.route("/profiles", methods=["POST"])
    async def create(req, resp, *, profile: ProfileForm = Form(...)):
        resp.media = profile.model_dump()

    spec = _spec_of(api)
    body = spec["paths"]["/profiles"]["post"]["requestBody"]
    assert body["required"] is True
    # The model's fields are the form fields — not a "profile" property.
    schema = body["content"]["application/x-www-form-urlencoded"]["schema"]
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"name", "age"}
    assert schema["properties"]["name"]["type"] == "string"
    assert schema["required"] == ["name"]
    assert "profile" not in schema["properties"]
    validate(spec)


def test_form_model_with_upload_file_documents_multipart():
    api = _api()

    class AvatarForm(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)

        name: str
        avatar: UploadFile

    @api.route("/avatars", methods=["POST"])
    async def create(req, resp, *, form: AvatarForm = Form(...)):
        resp.media = {"ok": True}

    spec = _spec_of(api)
    body = spec["paths"]["/avatars"]["post"]["requestBody"]
    assert body["required"] is True
    # An UploadFile field forces multipart; the file part is a binary string.
    schema = body["content"]["multipart/form-data"]["schema"]
    assert schema["properties"]["avatar"] == {"type": "string", "format": "binary"}
    assert schema["properties"]["name"]["type"] == "string"
    validate(spec)


def test_form_model_optional_marker_body_not_required():
    api = _api()

    @api.route("/profiles", methods=["POST"])
    async def create(req, resp, *, profile: ProfileForm = Form(None)):
        resp.media = {"ok": True}

    spec = _spec_of(api)
    body = spec["paths"]["/profiles"]["post"]["requestBody"]
    assert "required" not in body
    validate(spec)


def test_form_model_hoists_nested_defs_into_components():
    api = _api()

    class Color(enum.Enum):
        red = "red"
        blue = "blue"

    class PaintForm(BaseModel):
        name: str
        color: Color

    @api.route("/paints", methods=["POST"])
    async def create(req, resp, *, paint: PaintForm = Form(...)):
        resp.media = {"ok": True}

    spec = _spec_of(api)
    schemas = spec["components"]["schemas"]
    assert "Color" in schemas
    assert "$defs" not in str(spec)
    prefix = "#/components/schemas/"
    for ref in _collect_refs(spec, []):
        assert ref.startswith(prefix)
        assert ref.removeprefix(prefix) in schemas, f"dangling ref: {ref}"
    validate(spec)


def test_form_model_same_named_defs_get_distinct_components():
    api = _api()

    def make_form(colors):
        Color = enum.Enum("Color", colors)  # noqa: N806

        class PickForm(BaseModel):
            color: Color

        return PickForm

    FormA = make_form({"red": "red"})  # noqa: N806
    FormB = make_form({"green": "green"})  # noqa: N806

    @api.route("/a", methods=["POST"])
    async def a(req, resp, *, pick: FormA = Form(...)):
        resp.media = {"ok": True}

    @api.route("/b", methods=["POST"])
    async def b(req, resp, *, pick: FormB = Form(...)):
        resp.media = {"ok": True}

    spec = _spec_of(api)
    schemas = spec["components"]["schemas"]
    # Two distinct enums named Color must not merge into one component.
    assert "Color" in schemas
    assert "Color_2" in schemas
    prefix = "#/components/schemas/"
    for ref in _collect_refs(spec, []):
        assert ref.removeprefix(prefix) in schemas, f"dangling ref: {ref}"
    validate(spec)


def test_form_model_merges_with_scalar_form_and_file_markers():
    api = _api()

    @api.route("/mixed", methods=["POST"])
    async def create(
        req,
        resp,
        *,
        profile: ProfileForm = Form(...),
        note: str = Form("hi"),
        attachment=File(...),
    ):
        resp.media = {"ok": True}

    spec = _spec_of(api)
    body = spec["paths"]["/mixed"]["post"]["requestBody"]
    assert body["required"] is True
    # A File() marker alongside the model forces multipart.
    schema = body["content"]["multipart/form-data"]["schema"]
    assert set(schema["properties"]) == {"name", "age", "note", "attachment"}
    assert schema["properties"]["attachment"] == {
        "type": "string",
        "format": "binary",
    }
    # Model-required fields and the required File() are listed; Form("hi") isn't.
    assert set(schema["required"]) == {"name", "attachment"}
    validate(spec)
