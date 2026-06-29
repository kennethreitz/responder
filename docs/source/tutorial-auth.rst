Authentication
==============

Every API that handles user data needs authentication — a way to verify
who is making a request. This guide covers the most common patterns:
API keys, JWT tokens, and how to build reusable auth guards with
Responder's before-request hooks.


API Key Authentication
----------------------

The simplest approach. The client sends a secret key in a header, and
your server checks it against a known value. This is common for
server-to-server communication and simple APIs::

    API_KEYS = {"sk-abc123", "sk-def456"}

    @api.route(before_request=True)
    def check_api_key(req, resp):
        key = req.headers.get("X-API-Key")
        if key not in API_KEYS:
            resp.status_code = 401
            resp.media = {"error": "Invalid or missing API key"}

Because the before-request hook sets ``resp.status_code``, the route
handler is skipped entirely for unauthorized requests. The client never
reaches your endpoint — the guard catches them first.

The client sends the key like this::

    $ curl -H "X-API-Key: sk-abc123" http://localhost:5042/protected

.. note::

    To read a credential *inside* a route handler (rather than a guard), use
    a typed parameter marker — ``api_key: str = Header(None, alias="X-API-Key")``
    injects the validated header straight into the handler. See :doc:`tour`
    for the full set (:func:`~responder.Query`, :func:`~responder.Header`,
    :func:`~responder.Cookie`, :func:`~responder.Path`).


Bearer Token Authentication
----------------------------

Bearer tokens are the standard for modern APIs. The client sends a token
in the ``Authorization`` header, and the server validates it. The most
common format is `JWT <https://jwt.io/>`_ (JSON Web Tokens).

Install PyJWT::

    $ uv pip install pyjwt

Create a helper to encode and decode tokens::

    import jwt
    from datetime import datetime, timedelta, timezone

    SECRET = "your-secret-key"  # load from the environment in production

    def create_token(user_id: int) -> str:
        payload = {
            "sub": user_id,
            "exp": datetime.now(timezone.utc) + timedelta(hours=24),
        }
        return jwt.encode(payload, SECRET, algorithm="HS256")

    def verify_token(token: str) -> dict | None:
        try:
            return jwt.decode(token, SECRET, algorithms=["HS256"])
        except jwt.InvalidTokenError:
            return None

Add a login endpoint that issues tokens, and a before-request hook that
verifies them::

    @api.route("/login", methods=["POST"])
    async def login(req, resp):
        data = await req.media()
        # In a real app, check credentials against a database
        if data.get("username") == "admin" and data.get("password") == "secret":
            token = create_token(user_id=1)
            resp.media = {"token": token}
        else:
            resp.status_code = 401
            resp.media = {"error": "Invalid credentials"}

    @api.route(before_request=True)
    def auth_guard(req, resp):
        # Skip auth for the login endpoint itself
        if req.url.path == "/login":
            return

        auth = req.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            resp.status_code = 401
            resp.media = {"error": "Missing bearer token"}
            return

        token = auth[7:]  # Strip "Bearer "
        payload = verify_token(token)
        if payload is None:
            resp.status_code = 401
            resp.media = {"error": "Invalid or expired token"}
            return

        # Store the authenticated user on the request state
        req.state.user_id = payload["sub"]

Now any route can access the authenticated user::

    @api.route("/me")
    def get_me(req, resp):
        resp.media = {"user_id": req.state.user_id}

The client flow:

1. ``POST /login`` with credentials → receive a token
2. Include ``Authorization: Bearer <token>`` on every subsequent request
3. The token expires after 24 hours — the client must log in again

For APIs that use a single auth scheme on selected routes, Responder's auth
helpers are more direct than a global guard::

    from responder.ext.auth import BearerAuth

    bearer = BearerAuth(verify=lambda token: users.get(token))

    @api.route("/me", auth=bearer)
    def get_me(req, resp, *, user):
        resp.media = {"user": user}

``auth=`` enforces the scheme, sends the correct ``WWW-Authenticate`` challenge
on failure, documents the route in OpenAPI when OpenAPI is enabled, and injects
the verified principal into a ``user``, ``principal``, or ``auth`` parameter.


Skipping Auth for Public Routes
--------------------------------

For APIs where most routes require the same auth scheme, put it on the app and
mark public routes explicitly::

    bearer = BearerAuth(verify=lambda token: users.get(token))
    api = responder.API(auth=bearer)

    @api.post("/login", auth=None)
    def login(req, resp):
        resp.media = {"token": issue_token()}

    @api.get("/me")
    def me(req, resp, *, user):
        resp.media = {"user": user}

Routes inherit ``API(auth=...)`` by default. Passing ``auth=None`` on a route
makes that route public and removes the inherited OpenAPI security requirement.


Custom Exception for Auth Errors
---------------------------------

For a one-off rejection, reach for :func:`~responder.abort` — it raises a
rendered HTTP error from anywhere in the request path (handler, hook, or
dependency) without importing Starlette::

    from responder import abort

    @api.route(before_request=True)
    def auth_guard(req, resp):
        if req.url.path in PUBLIC_PATHS:
            return
        if "Authorization" not in req.headers:
            abort(401, detail="Missing authorization header")

``abort`` renders as JSON for ``Accept: application/json`` clients and plain
text otherwise.

When you want a *structured* error payload, define a custom exception and
register a handler instead::

    class AuthError(Exception):
        def __init__(self, message="Unauthorized", status_code=401):
            self.message = message
            self.status_code = status_code

    @api.exception_handler(AuthError)
    async def handle_auth_error(req, resp, exc):
        resp.status_code = exc.status_code
        resp.media = {"error": exc.message}

Now your auth guard can simply raise::

    @api.route(before_request=True)
    def auth_guard(req, resp):
        if req.url.path in PUBLIC_PATHS:
            return
        if "Authorization" not in req.headers:
            raise AuthError("Missing authorization header")

The decorator delegates to
``api.add_exception_handler(AuthError, handle_auth_error)`` — call that form
directly when you wire handlers from a setup function rather than at import
time.


Using Sessions for Web Apps
----------------------------

For traditional web applications (with HTML pages and forms), cookie-based
sessions are simpler than tokens. The browser handles cookies automatically
— no client-side token management needed::

    from responder.ext.sessions import regenerate_session

    @api.route("/login", methods=["POST"])
    async def login(req, resp):
        data = await req.media("form")
        if data["username"] == "admin" and data["password"] == "secret":
            regenerate_session(req)              # rotate the id on login
            resp.session["user"] = data["username"]
            # Honor ?next=, but never bounce off-site (open-redirect guard).
            target = req.params.get("next", "/dashboard")
            api.redirect(resp, location=target, allow_external=False)
        else:
            resp.status_code = 401
            resp.html = "<p>Invalid credentials</p>"

    @api.route("/dashboard")
    def dashboard(req, resp):
        user = req.session.get("user")
        if not user:
            api.redirect(resp, location="/login")
            return
        resp.html = f"<h1>Welcome, {user}!</h1>"

    @api.route("/logout")
    def logout(req, resp):
        resp.session.clear()
        api.redirect(resp, location="/login")

Two security details in that login handler are worth calling out:

- **Rotate the session id on login.** ``regenerate_session(req)`` issues a
  fresh id after the privilege change, defeating session fixation. It takes
  effect with a server-side ``session_backend`` (see :doc:`guide-config`);
  for the default signed-cookie sessions the cookie *is* the session, so the
  call is a harmless no-op.
- **Guard user-controlled redirects.** ``api.redirect`` allows external URLs
  by default; pass ``allow_external=False`` for any target derived from user
  input (like ``?next=``) so an attacker can't craft
  ``?next=https://evil.example`` to bounce a freshly authenticated user
  off-site. An external target then returns ``400``.

Sessions are secure by default — just set a stable secret key in
production::

    api = responder.API(secret_key="<a long, random string>")

Generate one with::

    $ python -c "import secrets; print(secrets.token_urlsafe(32))"

Better still, supply it out of band via the ``RESPONDER_SECRET_KEY``
environment variable so it never lands in source control.

.. note::

    The old public default is gone: ``secret_key="NOTASECRET"`` now raises
    :class:`~responder.ext.sessions.SessionConfigError`. With the default
    ``sessions="auto"``, omitting a key mints a random per-process key and
    logs a loud warning — fine for a quick local run, but it breaks across
    restarts and multiple workers (everyone gets logged out). Set a real key
    for anything you deploy. The cookie is also ``Secure`` in production by
    default; pass ``session_https_only=False`` only if you genuinely serve
    plain HTTP. See :doc:`guide-config` for the full set of session knobs
    (``session_cookie``, ``session_https_only``, ``session_same_site``,
    ``session_max_age``, ``session_backend``).

Session data is signed, not encrypted — clients can read it but can't forge
it. Never store secrets like passwords in the session.


Role-Based Access Control
--------------------------

For APIs where different users have different permissions, embed the
role in the token and check it in route-specific guards::

    def create_token(user_id: int, role: str = "user") -> str:
        payload = {
            "sub": user_id,
            "role": role,
            "exp": datetime.now(timezone.utc) + timedelta(hours=24),
        }
        return jwt.encode(payload, SECRET, algorithm="HS256")

Create a helper that checks for a specific role::

    from responder.types import Hook

    def require_role(*roles) -> Hook:
        """Before-request hook factory that restricts by role."""
        def check(req, resp):
            user_role = getattr(req.state, "role", None)
            if user_role not in roles:
                resp.status_code = 403
                resp.media = {"error": "Insufficient permissions"}
        return check

Use it on specific routes::

    @api.route("/admin/users", before=require_role("admin"))
    def list_all_users(req, resp):
        resp.media = {"users": [...]}

And store the role during token verification::

    # In your auth_guard:
    req.state.user_id = payload["sub"]
    req.state.role = payload.get("role", "user")


Choosing an Auth Strategy
--------------------------

- **API keys** — simplest. Good for server-to-server, CLI tools, and
  internal services. No expiration unless you build it.
- **JWT tokens** — standard for SPAs and mobile apps. Stateless, so
  they scale well. Downside: you can't revoke them without a blocklist.
- **Sessions** — best for traditional web apps with HTML forms. The
  browser manages cookies automatically. Stateful — the server controls
  the session lifecycle.

Start with API keys for internal tools, JWT for public APIs, and
sessions for web apps with login pages.
