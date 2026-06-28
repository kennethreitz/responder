# Migrating to Responder 5.0

Responder 5 layers fully type-driven request/response I/O, composable dependency
injection, plan-driven OpenAPI, secure-by-default sessions, and a deferred
middleware stack onto the unchanged `(req, resp)` core. The new typed features
are **additive sugar** — your existing handlers keep working — but v5 makes a
handful of deliberate breaking changes, listed here with the one-line fix.

## Sessions

| Change | What to do |
|---|---|
| `secret_key` no longer defaults to the public `"NOTASECRET"`; `API()` mints a random per-process key | Set `API(secret_key=…)` or the `RESPONDER_SECRET_KEY` env var for stable, multi-worker sessions (a startup warning fires until you do). |
| `API(secret_key="NOTASECRET")` raises `SessionConfigError` | Generate a real key: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| Session cookies are `Secure` by default in production | No action behind a TLS proxy; pass `session_https_only=False` only if you genuinely serve plain HTTP (e.g. local dev, tests). |
| `req.session` / `resp.session` raise `RuntimeError` when `sessions=False` | Re-enable sessions, or stop reading the session under the explicit opt-out. |
| `sessions=True` with no key raises | Provide a key, or use `sessions="auto"` (the default) to auto-generate an ephemeral one. |

Server-side sessions now slide their TTL via `touch`/`atouch` on read-only
requests (no behavior change for you; implement `touch` on a custom backend for
the cheaper path).

## `req.method` is uppercase

`req.method` now returns `"GET"`, not `"get"` (matching Flask/FastAPI/Starlette).
For one deprecation cycle it compares case-insensitively, so `req.method == "get"`
keeps working **with a `DeprecationWarning`**. Uppercase your literals.

Hash-based membership is the one thing the shim can't save: `req.method in {"get"}`
and `{"get": …}[req.method]` miss silently — use `==`, a tuple/list, or
uppercase keys.

## Middleware & errors

| Change | What to do |
|---|---|
| `API.app` is a lazily-built read-only property, not a writable attribute | Mutate via `api.add_middleware(Cls, **opts)` (now valid post-construction), or wrap the API object: `asgi = MyMiddleware(api)`. |
| User middleware now sits inside `ServerErrorMiddleware` | Its exceptions are now caught and rendered as `500`s. To wrap *everything*, wrap the API object instead of `add_middleware`. |
| Session writes are not persisted on an unhandled `500` | Persist explicitly before raising if you need it. |

`X-Request-ID` now appears on error/`500` responses too, and
`api.add_exception_handler(exc_or_status, handler)` is now a first-class method.

## Typed handler I/O

| Change | What to do |
|---|---|
| A Pydantic return annotation (`-> Model`) is now honored as `response_model` | Make the returned dict conform, or pass `@api.route(..., response_model=False)` *(if you don't want validation)*. |
| A body-model parameter beats a same-named dependency | Rename the dependency, or give the parameter a default. |
| `Query`/`Header`/`Cookie`/`Path` are reserved top-level names | Only affects `from responder import *`; explicit imports are unaffected. |

New: `def search(req, resp, *, q: str = Query(...), token: str = Header(None))`
injects validated query params, headers, cookies, and path params.

## Dependency injection

| Change | What to do |
|---|---|
| A provider receives the request only via a `req`/`request` parameter or a `Request`/`WebSocket` annotation | Rename a nonstandard request parameter to `req`, or annotate it `Request`. A sole-unnamed-param provider still works for one cycle (with a `DeprecationWarning`). |
| `req`/`request`/`resp`/`response`/`ws`/`websocket` are reserved dependency names | Rename any dependency registered under these. |
| Misconfigured graphs raise `DependencyError` subclasses | Cycles, unknown params, and scope violations now surface as `DependencyCycleError` / `DependencyResolutionError` / `DependencyScopeError`. |

New: a provider can depend on other providers (recursive, memoized,
reverse-topological teardown, cycle detection).

## OpenAPI

| Change | What to do |
|---|---|
| Routes without a docstring/model now appear in the schema | Pass `@api.route(..., include_in_schema=False)` to hide a route (schema/docs/static/metrics are auto-excluded). |
| Docstring YAML is deep-merged onto a generated base | Fully-specified docstrings are unaffected; the generated parameters/request/response are added underneath. |

The spec is now generated from each route's methods, models, and
`Query`/`Header`/`Cookie` markers, with an automatic `422` for validating routes.
