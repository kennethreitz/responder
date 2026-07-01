"""Inline Depends(...) can now nest inside a provider (sub-dependency chains),
matching the FastAPI pattern. Previously only named @api.dependency() providers
could chain; a provider with an inline Depends default raised
DependencyResolutionError."""

import responder
from responder import Depends


def _api():
    return responder.API(allowed_hosts=[";"], session_https_only=False)


def test_inline_depends_nests_inside_provider():
    api = _api()

    def get_db():
        return "db-conn"

    def get_user(db=Depends(get_db)):
        return f"user@{db}"

    @api.route("/me")
    def me(req, resp, *, user=Depends(get_user)):
        resp.text = user

    assert api.requests.get("/me").text == "user@db-conn"


def test_inline_depends_deeper_chain_and_request_access():
    api = _api()

    def get_prefix():
        return "id"

    def get_token(req, prefix=Depends(get_prefix)):
        return f"{prefix}:{req.headers.get('X-Token', 'anon')}"

    def get_principal(token=Depends(get_token)):
        return {"token": token}

    @api.route("/who")
    def who(req, resp, *, principal=Depends(get_principal)):
        resp.media = principal

    r = api.requests.get("/who", headers={"X-Token": "abc"})
    assert r.json() == {"token": "id:abc"}
