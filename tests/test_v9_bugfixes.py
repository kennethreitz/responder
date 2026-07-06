"""Regression tests for bugs found in the post-v9.0.0 adversarial scan.

Each test pins a specific defect; see the matching commit for the fix.
"""

import responder


def _api(**kwargs):
    kwargs.setdefault("allowed_hosts", [";"])
    kwargs.setdefault("session_https_only", False)
    kwargs.setdefault("secret_key", "x" * 32)
    return responder.API(**kwargs)


def _url(s):
    return f"http://;{s}"


# --- CSRF: non-ASCII submitted token must 403, not 500 ----------------------


def test_csrf_non_ascii_token_is_403_not_500():
    api = _api(csrf=True)

    @api.route("/token")
    async def token(req, resp):
        resp.media = {"token": req.csrf_token}

    @api.route("/submit", methods=["POST"])
    async def submit(req, resp):
        resp.media = {"ok": True}

    client = api.requests
    client.get(_url("/token"))  # establish a session token

    # A non-ASCII token in the form field is fully client-controlled; it must
    # be rejected cleanly (403), not blow up hmac.compare_digest into a 500.
    r = client.post(_url("/submit"), data={"csrf_token": "häh"})
    assert r.status_code == 403

    # And a valid multipart csrf_token still passes (the fix must not break it).
    good = client.get(_url("/token")).json()["token"]
    r = client.post(_url("/submit"), data={"csrf_token": good})
    assert r.status_code == 200
