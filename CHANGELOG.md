# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v5.0.0] - 2026-06-28

A major release: fully type-driven request/response I/O, composable dependency
injection, plan-driven OpenAPI, secure-by-default sessions, and a deferred
middleware stack — layered onto the unchanged `(req, resp)` core. Breaking
changes are staged behind the [v5 migration guide](docs/migration-v5.md).

### Added

- **Type-hint-driven OpenAPI.** The schema is now generated from each route's
  methods, body/response models, and `Query`/`Header`/`Cookie` markers — so
  routes **without** a YAML docstring now appear in the spec (with parameters,
  request/response schemas, and an automatic `422` for validating routes).
  Docstring YAML is deep-merged on top as an override. New
  `@api.route(..., include_in_schema=False)` hides a route; internal routes
  (schema/docs/static/metrics) are auto-excluded.
- **Typed parameter markers.** `Query()`, `Header()`, `Cookie()`, and `Path()`
  (exported from `responder`) inject validated, type-coerced query parameters,
  headers, cookies, and path parameters as handler arguments —
  `def search(req, resp, *, q: str = Query(...), limit: int = Query(10))` —
  with `422` on a missing required value or a validation failure. Supports
  defaults, aliases, and sequence types (`list[int]` from repeated query keys).
- **Return annotation as `response_model`.** A Pydantic return annotation
  (`def handler(req, resp) -> ItemOut`) now validates/serializes the response
  against that model (coerce types, strip undeclared fields), the same as an
  explicit `response_model=`.
- **Composable dependency injection.** A dependency provider can now depend on
  *other* providers by declaring them as parameters — resolved recursively,
  memoized across the whole request graph, torn down in reverse-topological
  order, with cycle detection. App-scoped dependencies may compose with other
  app-scoped ones (but never with the request or request-scoped deps).
  Providers receive the request only via a `req`/`request` parameter or a
  `Request`/`WebSocket` annotation (a sole-unnamed-param shim warns for one
  cycle). New `DependencyError`/`DependencyCycleError`/`DependencyScopeError`/
  `DependencyResolutionError` exceptions; `req`/`request`/`resp`/`response`/
  `ws`/`websocket` are reserved dependency names.
- **Async-native backends.** Session and rate-limit backends may now expose
  async methods (`aget`/`aset`/`adelete`/`atouch`, `ahit`) that are awaited
  directly instead of run in a thread — with `AsyncRedisSessionBackend` and
  `AsyncRedisBackend` built in (`redis.asyncio`). `RateLimiter.acheck()` and
  `.install()` work with any backend. Backend `Protocol`s are exported for
  typing.
- **Sliding server-side session TTL via `touch`.** An unchanged read-only
  request now refreshes the backend TTL with `touch`/`atouch` (no full
  re-serialize) while a mutation still does a full `set` — fixing the
  premature-logout/dirty-tracking trade-off correctly.
- **`api.add_exception_handler(exc_or_status, handler)`** registers an error
  handler programmatically (the `@api.exception_handler` decorator now delegates
  to it). A handler for `500`/`Exception` installs the catch-all server-error
  handler.
- **`api.add_middleware()` now works after construction** — middleware is
  collected and the stack is built lazily on first request.

### Security

- **Secure-by-default sessions.** The session signing key no longer defaults to
  the public `"NOTASECRET"`. With `sessions="auto"` (the default) Responder uses
  your `secret_key` / `RESPONDER_SECRET_KEY`, else mints a random per-process key
  with a loud warning (set a key for stable, multi-worker sessions).
  `secret_key="NOTASECRET"` now raises. Session cookies are `Secure` by default
  in production (`session_https_only=None`), and `SameSite=None` without Secure
  is rejected.
- **`sessions=` and `session_max_age=` knobs.** `sessions=True` requires a real
  key (raises otherwise); `sessions=False` disables session middleware entirely
  and makes `req.session`/`resp.session` raise a guiding `RuntimeError`.

### Changed

- `resp.session` is now a property that delegates to `req.session` (so
  `resp.session = {...}` actually writes through to the cookie). Reading either
  with sessions disabled raises `RuntimeError`.
- **Deferred middleware stack.** The ASGI stack is assembled lazily with
  `ServerErrorMiddleware` as the outermost application layer (so it catches
  errors from *any* middleware — sessions, CORS, user middleware) while the
  logging/request-id tier wraps even it, so `X-Request-ID` and the real status
  now appear on `500` responses. `API.app` is a lazy read-only property; mutate
  the stack via `add_middleware()` or wrap the API object for a truly-outermost
  layer. User middleware now sits inside `ServerErrorMiddleware` (its errors are
  caught), and sessions sit below it (a write is not persisted on an unhandled
  `500`).
- **`req.method` is now UPPERCASE** (`"GET"` not `"get"`), matching
  Flask/FastAPI/Starlette/stdlib. For one deprecation cycle it returns an
  `HTTPMethod` (a `str` subclass) that still compares case-insensitively — so
  `req.method == "get"` keeps working but emits a `DeprecationWarning`. Hash-
  based membership (`req.method in {"get"}`) is case-sensitive and misses; use
  `==`, a tuple/list, or uppercase keys. Removed in Responder 6.0.

## [v4.1.0] - 2026-06-28

A backward-compatible quality release: verified correctness and
resource-safety fixes, additive security hardening, and developer-experience
improvements. No existing call signatures change.

### Added

- **`responder.types`**: public `Handler`, `Hook`, and `Dependency` type
  aliases for annotating your own handlers and hooks.
- The `req`/`resp` hot surface (`headers`, `params`, `method`, `cookies`,
  `mimetype`, `is_json`, `is_secure`, …) and the dynamic `status_codes`
  constants are now statically typed, so the shipped `py.typed` marker actually
  helps downstream type checkers (and removes internal `type: ignore`s).
- **Type-hint-driven body injection**: a handler parameter annotated with a
  Pydantic model now receives the validated request body —
  `async def create(req, resp, *, item: ItemIn)` — returning `422` on invalid
  input. Works for sync and async handlers and coexists with path parameters
  and dependencies. (Previously such a signature raised a `500`.) Use an
  explicit `response_model=` for response validation.
- **First-class Pydantic models**: `resp.media = model` and `return Model()`
  now serialize correctly (across JSON, YAML, and MessagePack), as do
  dataclasses. The JSON encoder also handles `datetime`, `date`, `time`,
  `UUID`, `Decimal`, `set`, and `bytes` natively — `resp.media =
  {"created_at": datetime.now()}` no longer 500s.
- **Pluggable type encoder**: `API(encoder=...)` accepts an `obj ->
  serializable` callable applied across **all** response formats (JSON, YAML,
  MessagePack) to serialize custom types. It's tried first and falls back to
  the built-in conversions.
- **Flask-style tuple returns**: a handler may `return body, status` or
  `return body, status, headers` (previously these were silently dropped).
- **`responder.abort(status_code, *, detail=None, headers=None)`** raises a
  rendered HTTP error from anywhere in a handler or dependency, without
  importing Starlette.
- **Bare lifecycle decorators**: `@api.before_request` and `@api.after_request`
  now work without the parentheses, alongside the existing called forms.
- **Sync body access**: `req.media_sync()` and `req.text_sync` let synchronous
  handlers read the request body (they bridge to the loop from the worker
  thread Responder already runs sync handlers in).

### Changed

- **`response_model` now fails closed instead of leaking.** Previously a
  response whose data didn't satisfy its declared `response_model` was sent
  through *unvalidated* (wrong types, undeclared fields and all). It now
  coerces and strips as documented, and on a genuine validation failure raises
  in debug mode or returns a `500` (never the unvalidated payload) in
  production. Valid responses are unaffected; `datetime`/`UUID`/etc. fields now
  serialize instead of 500-ing. List responses are validated item-by-item.
- Malformed request bodies now return **400** instead of **500**: invalid
  JSON, YAML, or MessagePack raise a rendered `400`, and binary parts in a
  `req.media("form")` call are skipped rather than crashing.
- `API(auto_escape=...)` is now actually forwarded to the template environment
  (it was previously ignored).
- `resp.ok` reads as `200`-based success until a status code is set, instead of
  raising.
- Per-request overhead trimmed: coroutine-function detection for views and
  hooks is memoized, and `req.params` reads the raw query string from the ASGI
  scope instead of rebuilding and re-parsing the full URL.

### Fixed

- **Mounted-app routing**: a mount prefix now only matches on a path-segment
  boundary, so e.g. `GET /subscribe` is no longer mis-routed into an app
  mounted at `/sub` (and sub-paths keep their leading slash). More specific
  (longer) mount prefixes resolve first.
- **Dependency teardown leaks**: a failing generator-dependency teardown no
  longer skips the remaining teardowns — each runs best-effort and failures
  are logged, so connections/files/locks are always released. Applies to HTTP
  routes, WebSocket routes, and app-scoped shutdown.
- **Event-loop blocking**: server-side session backends (e.g. Redis) now run
  their `get`/`set`/`delete` off the event loop, `resp.file()` reads file
  bytes in a worker thread, and the static `index.html` fallback reads off the
  loop too — so these no longer stall the server from `async` handlers.
- **Request-size enforcement**: `max_request_size` is now enforced while the
  body streams in (chunked or lying `Content-Length`) instead of buffering the
  whole body first; also fixes an empty-body re-read.
- `BackgroundQueue.__call__` no longer wraps work in a pointless
  create-task-then-await; its await-to-completion semantics are now documented
  (use `.task`/`.run` for fire-and-forget).

### Security

- **Default secret key warning**: Responder now logs a loud warning when cookie
  sessions are signed with the built-in `"NOTASECRET"` default key (outside
  debug mode) — forged session data is otherwise trivial. Set
  `API(secret_key=...)`.
- **Session fixation defense**: server-side sessions mint a fresh id when a
  presented cookie doesn't resolve, and a new `regenerate_session(req)` helper
  (`responder.ext.sessions`) rotates the id after login/privilege change.
- **Session cookie controls**: `API(session_cookie=..., session_https_only=...,
  session_same_site=...)` configure the session cookie for both cookie-payload
  and server-side sessions.
- **GraphQL hardening**: `api.graphql(..., graphiql=..., introspection=...,
  max_depth=...)` can disable the in-browser IDE, reject schema-introspection
  queries, and cap query nesting depth (a DoS guard) for production.
- **Path-traversal jail**: `resp.file(path, root=...)`, `resp.stream_file(...,
  root=...)`, and `resp.download(..., root=...)` resolve `path` under `root` and
  return `404` on any `..`/symlink escape — use whenever the path is user input.
- **Open-redirect guard**: `resp.redirect(location, allow_external=False)` (and
  `api.redirect(...)`) refuse to redirect to an absolute or protocol-relative
  URL — use for user-supplied locations.

## [v4.0.0] - 2026-06-12

### Changed

- **Breaking:** Slimmed the default install from ~60 packages to ~30 by
  moving heavy dependencies behind extras:
  - `pueblo[sfa-full]` (which pulls in `s3fs`, `aiobotocore`, `aiohttp`,
    `libarchive-c`, and friends) is now the `cli` extra. `responder run`
    still works out of the box for local modules and file paths
    (`app:api`, `myapp/core.py`); only remote targets (URLs, `github://`,
    cloud storage) need `pip install 'responder[cli]'`
  - `graphene` and `graphql-core` are now the `graphql` extra. Install
    with `pip install 'responder[graphql]'` to use `api.graphql()`

## [v3.12.0] - 2026-06-12

### Added

- Built-in metrics: `API(metrics_route="/metrics")` serves request counts
  and latency histograms in Prometheus text format, zero dependencies.
  Labels use route patterns (`/users/{id}`) so cardinality stays bounded;
  error responses are recorded with their real status codes
- Server-side sessions: `API(session_backend=...)` stores session data in
  a backend (`MemorySessionBackend`, `RedisSessionBackend`, or any object
  with `get`/`set`/`delete`) with only an opaque ID in the cookie —
  enabling revocation and unbounded session size. Handler code is unchanged
- Query-parameter validation: `@api.route(..., params_model=Model)`
  coerces and validates query strings with Pydantic (`422` on failure),
  exposes the instance as `req.state.validated_params`, maps repeated keys
  to `list` fields, and documents the parameters in the OpenAPI spec
- `resp.render(template, **context)` — render a Jinja2 template as the
  HTML response body in one call

## [v3.11.0] - 2026-06-11

### Added

- HTTP range requests: `resp.file()` and `resp.stream_file()` answer
  `Range: bytes=...` with `206 Partial Content` (suffix and open-ended
  ranges, `416` for unsatisfiable, `Accept-Ranges` advertised) — enables
  video seeking and resumable downloads
- `resp.download(path, filename=...)` serves files as attachments with
  proper `Content-Disposition` (RFC 5987 encoding for non-ASCII names),
  streamed and resumable
- Request timeouts: `API(request_timeout=seconds)` answers overrunning
  handlers with `504 Gateway Timeout` (content-negotiated); dependency
  teardowns still run

### Performance

- Route resolution is cached per (method, path) with invalidation on
  registration — ~10% faster dispatch at 81 routes, growing with route
  count

## [v3.10.0] - 2026-06-11

### Added

- Trailing-slash redirects: requests that miss only by a trailing slash get
  a `307` to the canonical path, preserving method and query string.
  Disable with `API(redirect_slashes=False)`
- Request size limits: `API(max_request_size=bytes)` returns `413` for
  oversized bodies — fast-fails on `Content-Length` and enforces
  cumulatively for chunked/streamed uploads
- Automatic ETags: `API(auto_etag=True)` adds a content-hash `ETag` to GET
  responses with full `304 Not Modified` handling; an explicit `resp.etag`
  always wins
- After-response background tasks: `resp.background(func, *args)` defers
  work until the client has the response (sync and async, ordered)
- `resp.cache_control(...)` helper for building `Cache-Control` headers

### Fixed

- A `413` raised while reading the body during `request_model` validation
  is no longer swallowed into a `422`

### Changed

- Trailing-slash redirects are on by default (previously such requests
  were 404s)

## [v3.9.1] - 2026-06-11

### Added

- Conditional request support: set `resp.etag` or `resp.last_modified` and
  matching `If-None-Match`/`If-Modified-Since` requests automatically get
  `304 Not Modified` (RFC 7232 semantics: `If-None-Match` precedence, weak
  comparison, GET/HEAD only)
- Request body streaming: `async for chunk in req.stream()` iterates large
  uploads without buffering
- Pluggable rate-limiter backends: `RateLimiter(backend=...)` with the
  in-memory default plus a new `RedisBackend` for multi-process deployments
- Application state: `api.state` namespace, reachable from handlers via
  `req.api.state`
- `req.api` is now populated with the owning `API` instance (it was always
  `None` before)

### Fixed

- `API(static_dir=None)` crashed on every route registration — the static
  fallback assertion now only applies when no endpoint is given
- The static-fallback error is a `ValueError` instead of a bare `assert`

### Performance

- Request headers are parsed into the case-insensitive dict lazily, on
  first access (~5% faster dispatch on header-heavy requests that don't
  read headers)

## [v3.9.0] - 2026-06-11

### Added

- Dependency injection for WebSocket handlers: declare path parameters and
  registered dependencies by name after the `ws` argument. Request-scoped
  providers taking a parameter receive the WebSocket; generator teardown
  runs when the handler finishes. Handlers that only take `ws` are unaffected
- OpenAPI 3.1 support (`openapi="3.1.0"`)
- The OpenAPI schema endpoint now serves JSON when requested via
  `Accept: application/json`, or always when `openapi_route` ends in `.json`
- Path parameters are documented automatically in the OpenAPI spec from
  route patterns (`{id:int}` → required integer parameter)
- Built-in error responses (404, 405) are content-negotiated: JSON clients
  receive `{"error": ...}` bodies instead of plain text

### Fixed

- OpenAPI paths no longer leak convertor patterns (`/users/{id:int}` is now
  emitted as the spec-compliant `/users/{id}`)
- Registering a duplicate route now raises `ValueError` (previously an
  `assert` that disappears under `python -O`)
- Removed dead `_exception_handlers` bookkeeping in `API.exception_handler`

### Changed

- `mypy` now passes with zero errors across the codebase (was 25); `ruff`
  is clean as well
- `types-pyyaml` added to the `test` extra

## [v3.8.0] - 2026-06-11

### Added

- Handlers can return values: a `dict`/`list` sets `resp.media`, a `str` sets
  `resp.text`, and `bytes` set `resp.content`. Returning `None` keeps the
  mutate-`resp` behavior, so existing handlers are unaffected
- App-scoped dependencies: `@api.dependency(scope="app")` resolves the
  provider once on first use and caches it for the application's lifetime;
  generator teardown runs at shutdown
- Automatic `OPTIONS` responses with an `Allow` header for method-restricted routes
- `HEAD` requests are accepted wherever `GET` is
- `set_cookie()` gains a `samesite` parameter, defaulting to `"lax"`
- The validated `request_model` instance is now available to handlers as
  `req.state.validated` — no need to re-parse the body

### Changed

- Requests to an existing path with an unsupported method now return
  `405 Method Not Allowed` with an `Allow` header (previously 404)
- `RouteGroup.before_request` hooks are now scoped to the group's prefix
  (previously they silently applied to every route)

### Performance

- View signature inspection for dependency injection is cached per function

## [v3.7.0] - 2026-06-11

### Added

- Dependency injection for route handlers: register providers with
  `@api.dependency()` (or `api.add_dependency(name, provider)`) and declare
  them as view parameters by name. Supports sync/async functions and
  generators (code after `yield` runs as teardown once the response is sent).
  Providers accepting a parameter receive the current `Request`. Dependencies
  resolve at most once per request; path parameters take precedence.
- Per-route rate limiting via `RateLimiter.limit` decorator
- WebSocket before-request hooks can now reject connections: closing the
  socket in a hook short-circuits the route handler
- WebSocket before-request hooks may now be sync functions (run in the threadpool)
- Custom formats registered on `api.formats` now actually reach request
  parsing and response negotiation (previously each request got a fresh
  default format registry)

### Fixed

- `{value:float}` path convertor matched garbage like `1a5` (unescaped regex
  dot) and crashed with a 500 — now correctly returns 404
- Literal characters in route paths are now regex-escaped, so `/file.json`
  no longer matches `/fileXjson`
- Unbounded memory growth in `BackgroundQueue` — completed futures are now
  pruned from `results`
- `req.media("form")` crashed with a `TypeError` when the request had no
  `Content-Type` header
- Content negotiation returned an empty body for `Accept` headers matching
  encode-incapable formats (e.g. `multipart/form-data`) — now falls through
  to JSON

### Changed

- `Request.url` and `Request.params` are now computed once and cached
- Format registries are no longer rebuilt twice per request

## [v3.6.2] - 2026-04-12

### Fixed

- GraphQL error responses now correctly return 400 status instead of always 200
- OpenAPI docs UI now respects custom `openapi_route` instead of hardcoding `/schema.yml`
- `before_requests` default type mismatch that could crash routes called outside the router
- Blocking synchronous file I/O in `Response.stream_file()` — now uses async I/O via anyio
- Memory leak in rate limiter (empty bucket keys never cleaned up)
- Race condition in rate limiter `check()` — added thread-safe locking
- WSGI fallback catching all `TypeError`s instead of just call-signature mismatches
- Pydantic request/response model validation crashing on non-dict bodies
- Test assertions that could never fail (`or True`, `< 500` patterns)
- `CaseInsensitiveDict` missing `__delitem__`, `pop`, and `setdefault` overrides
- `assert` used for input validation in OpenAPI extension (stripped by `python -O`)
- Potential XSS in GraphiQL template endpoint injection
- Dead `or ""` in media format detection logic

### Changed

- `DELETE` requests now participate in Pydantic request body validation
- Simplified status code category check to use chained comparison

### Removed

- Unused `method` parameter from `load_target()`
- Unused Node.js setup step from CI test workflow

## [v3.6.1] - 2026-04-12

### Added

- Configurable GZip compression via `gzip` parameter on `API()` (defaults to `True`)

## [v3.6.0] - 2026-03-24

### Added

- Built-in structured logging with per-request context (`enable_logging=True`)
  - `api.log` — always-available logger, enriched with request context when logging is enabled
  - Automatic access logging with timing: `GET /path → 200 (1.2ms)`
  - Request ID generation/forwarding via `X-Request-ID` header
  - `contextvars`-based request context (ID, method, path, client IP) on every log record
  - `responder.ext.logging` module: `get_logger()`, `RequestContext`, `RequestContextFilter`
- CLAUDE.md project guide and `/release` command
- Version number in docs sidebar

### Changed

- Comprehensive documentation improvements across all pages
  - Deployment: health checks, Docker Compose, Caddy, Procfile, production checklist
  - API reference: usage examples for every class
  - Feature tour: Pydantic validation, content negotiation, structured logging sections
  - Tutorials: modernized SQLAlchemy to `mapped_column()`, fixed deprecated `datetime.utcnow()`,
    WebSocket `WebSocketDisconnect` handling, role-based auth, auth strategy guide
  - Testing: rate limiting and WSGI mount examples
  - Middleware: pure ASGI middleware example
  - Quickstart: links to all tutorials
  - Sandbox: full rewrite with project layout
- Docker example uses `uv` instead of pip
- Backlog updated: removed implemented features, replaced HTTP/2 server push with dependency injection

### Removed

- `uv.lock` — this is a library, not an application

## [v3.5.0] - 2026-03-24

### Added

- CI validation for Python 3.14, 3.14 free-threaded, and PyPy 3.11
- Marimo notebook mounting docs and example
- Type annotations for `routes.py`

### Changed

- Replaced deprecated `asyncio.iscoroutinefunction` with `inspect.iscoroutinefunction` ahead of Python 3.16 removal
- Narrowed broad `except Exception` to specific exceptions in response model serialization and websocket chat example
- Improved GraphQL API interface with expanded test coverage
- Code formatting cleanup via pyproject-fmt and ruff
- Dropped Python 3.9 from CI

### Fixed

- WSGI mount returning 400 when requesting the exact mount root path
- Werkzeug 3.1.7 compatibility for trusted host validation in tests
- `future.result` bare property access in background task test (now properly calls `future.result()`)
- OpenAPI template packaging and static file serving
- RST title underline warning breaking docs CI

### Removed

- Read the Docs configuration (docs hosted on GitHub Pages)

## [v3.4.0] - 2026-03-22

### Changed

- Upgraded to Starlette 1.0
- Added comprehensive docstrings across the codebase
- Expanded API reference documentation

## [v3.3.0] - 2026-03-22

### Added

- Full documentation rewrite: tutorials for REST APIs, SQLAlchemy, Flask migration
- Auth, WebSocket, middleware, and configuration guides
- Testing docs with prose, examples, and tips
- GitHub Pages deployment for docs

### Changed

- Reworked homepage prose
- Rewrote CLI and API reference docs

## [v3.2.0] - 2026-03-22

### Added

- Pydantic auto-validation: `request_model` validates input, returns 422 on failure
- Pydantic auto-serialization: `response_model` strips extra fields from responses
- Server-Sent Events: `@resp.sse` for real-time streaming
- `resp.stream_file()` for streaming large files without loading into memory
- `@api.after_request()` hooks
- `api.group("/prefix")` for route groups and API versioning
- `api.graphql("/path", schema=schema)` one-liner GraphQL setup
- `api = responder.API(request_id=True)` for automatic request ID generation
- Built-in rate limiter: `RateLimiter(requests=100, period=60).install(api)`
- MessagePack format support: `await req.media("msgpack")`
- `req.is_json`, `req.path_params`, `req.client` properties
- `api.exception_handler()` decorator for custom error handling
- Lifespan context manager support
- `uuid` and `path` route convertors
- PEP 561 `py.typed` marker
- Pydantic support for OpenAPI schema generation

### Changed

- Dependencies flattened: `pip install responder` gets everything
- Core deps reduced to starlette + uvicorn
- TestClient lazy-loaded (no httpx import in production)
- Before-request hooks can short-circuit by setting status code
- Removed poethepoet task runner

### Fixed

- Multipart parser losing headers when parts have multiple headers
- `url_for()` with typed route params (`{id:int}`)
- `resp.body` encoding crash on bytes content
- GraphQL text query missing `await`
- Streaming responses not sending Content-Type headers
- Python 3.9 compatibility for union type syntax

## [v3.0.0] - 2026-03-22

### Added

- Platform: Added support for Python 3.10 - Python 3.13
- CLI: `responder run` now also accepts a filesystem path on its `<target>`
  argument, enabling usage on single-file applications.
- CLI: `responder run` now also accepts URLs.

### Changed

- Platform: Minimum Python version is now 3.9 (dropped 3.6, 3.7, 3.8)
- Dependencies: Dramatically reduced core dependency count (10 → 5)
  - Removed `requests`, `requests-toolbelt`, `rfc3986`, `whitenoise`
  - Moved `apispec` and `marshmallow` to `openapi` optional extra
  - Replaced `rfc3986` with stdlib `urllib.parse`
  - Replaced `requests-toolbelt` multipart decoder with `python-multipart`
  - Replaced deprecated `starlette.middleware.wsgi` with `a2wsgi`
  - Switched from WhiteNoise to ServeStatic
- Dependencies: Pinned `starlette[full]>=0.40` (was unpinned)
- GraphQL: Upgraded to `graphene>=3` and `graphql-core>=3.1`
  (from `graphene<3` and `graphql-server-core`, which is unmaintained)
- GraphQL: Updated GraphiQL UI from 0.12.0 (2018) to 3.0.6 with React 18
- Extensions: All of CLI-, GraphQL-, and OpenAPI-Support modules are
  extensions now, found within the `responder.ext` module namespace.
- Packaging: Migrated from `setup.py` to declarative `pyproject.toml`

### Removed

- Platform: Removed support for EOL Python 3.6, 3.7, 3.8
- Status codes: Removed deprecated `resume_incomplete` and `resume`
  aliases for HTTP 308 (marked for removal in 3.0)
- CLI: `responder run --build` ceased to exist

### Fixed

- Routing: Fixed dispatching `static_route=None` on Windows
- uvicorn: `--debug` now maps to uvicorn's `log_level = "debug"`
- Tests: Fixed deprecated httpx TestClient usage

## [v2.0.5] - 2019-12-15

### Added

- Update requirements to support python 3.8

## [v2.0.4] - 2019-11-19

### Fixed

- Fix static app resolving

## [v2.0.3] - 2019-09-20

### Fixed

- Fix template conflicts

## [v2.0.2] - 2019-09-20

### Fixed

- Fix template conflicts

## [v2.0.1] - 2019-09-20

### Fixed

- Fix template import

## [v2.0.0] - 2019-09-19

### Changed

- Refactor Router and Schema

## [v1.3.2] - 2019-08-15

### Added

- ASGI 3 support
- CI tests for python 3.8-dev
- Now requests have `state` a mapping object

### Deprecated

- ASGI 2

## [v1.3.1] - 2019-04-28

### Added

- Route params Converters
- Add search for documentation pages

### Changed

- Bump dependencies

## [v1.3.0] - 2019-02-22

### Fixed

- Versioning issue
- Multiple cookies.
- Whitenoise returns not found.
- Other bugfixes.

### Added

- Stream support via `resp.stream`.
- Cookie directives via `resp.set_cookie`.
- Add `resp.html` to send HTML.
- Other improvements.

## [v1.1.3] - 2019-01-12

### Changed

- Refactor `_route_for`

### Fixed

- Resolve startup/shutdwown events

## [v1.2.0] - 2018-12-29

### Added

- Documentations

### Changed

- Use Starlette's LifeSpan middleware
- Update denpendencies

### Fixed

- Fix route.is_class_based
- Fix test_500
- Typos

## [v1.1.2] - 2018-11-11

### Fixed

- Minor fixes for Open API
- Typos

## [v1.1.1] - 2018-10-29

### Changed

- Run sync views in a threadpoolexecutor.

## [v1.1.0] - 2018-10-27

### Added

- Support for `before_request`.

## [v1.0.5]- 2018-10-27

### Fixed

- Fix sessions.

## [v1.0.4] - 2018-10-27

### Fixed

- Potential bufix for cookies.

## [v1.0.3] - 2018-10-27

### Fixed

- Bugfix for redirects.

## [v1.0.2] - 2018-10-27

### Changed

- Improvement for static file hosting.

## [v1.0.1] - 2018-10-26

### Changed

- Improve cors configuration settings.

## [v1.0.0] - 2018-10-26

### Changed

- Move GraphQL support into a built-in plugin.

## [v0.3.3] - 2018-10-25

### Added

- CORS support

### Changed

- Improved exceptions.

## [v0.3.2] - 2018-10-25

### Changed

- Subtle improvements.

## [v0.3.1] - 2018-10-24

### Fixed

- Packaging fix.

## [v0.3.0] - 2018-10-24

### Changed

- Interactive Documentation endpoint.
- Minor improvements.

## [v0.2.3] - 2018-10-24

### Changed

- Overall improvements.

## [v0.2.2] - 2018-10-23

### Added

- Show traceback info when background tasks raise exceptions.

## [v0.2.1] - 2018-10-23

### Added

- api.requests.

## [v0.2.0] - 2018-10-22

### Added

- WebSocket support.

## [v0.1.6] - 2018-10-20

### Added

- 500 support.

## [v0.1.5] - 2018-10-20

### Added

- File upload support

### Changed

- Improvements to sequential media reading.

## [v0.1.4] - 2018-10-19

### Fixed

- Stability.

## [v0.1.3] - 2018-10-18

### Added

- Sessions support.

## [v0.1.2] - 2018-10-18

### Added

- Cookies support.

## [v0.1.1] - 2018-10-17

### Changed

- Default routes.

## [v0.1.0] - 2018-10-17

### Added

- Prototype of static application support.

## [v0.0.10] - 2018-10-17

### Fixed

- Bugfix for async class-based views.

## [v0.0.9] - 2018-10-17

### Fixed

- Bugfix for async class-based views.

## [v0.0.8] - 2018-10-17

### Added

- GraphiQL Support.

### Changed

- Improvement to route selection.

## [v0.0.7] - 2018-10-16

### Changed

- Immutable Request object.

## [v0.0.6] - 2018-10-16

### Added

- Ability to mount WSGI apps.
- Supply content-type when serving up the schema.

## [v0.0.5] - 2018-10-15

### Added

- OpenAPI Schema support.
- Safe load/dump yaml.

## [v0.0.4] - 2018-10-15

### Added

- Asynchronous support for data uploads.

### Fixed

- Bug fixes.

## [v0.0.3] - 2018-10-13

### Fixed

- Bug fixes.

## [v0.0.2] - 2018-10-13

### Changed

- Switch to ASGI/Starlette.

## [v0.0.1] - 2018-10-12

### Added

- Conception!

[v5.0.0]: https://github.com/kennethreitz/responder/compare/v4.1.0..v5.0.0
[v4.1.0]: https://github.com/kennethreitz/responder/compare/v4.0.0..v4.1.0
[v4.0.0]: https://github.com/kennethreitz/responder/compare/v3.12.0..v4.0.0
[v3.12.0]: https://github.com/kennethreitz/responder/compare/v3.11.0..v3.12.0
[v3.11.0]: https://github.com/kennethreitz/responder/compare/v3.10.0..v3.11.0
[v3.10.0]: https://github.com/kennethreitz/responder/compare/v3.9.1..v3.10.0
[v3.9.1]: https://github.com/kennethreitz/responder/compare/v3.9.0..v3.9.1
[v3.9.0]: https://github.com/kennethreitz/responder/compare/v3.8.0..v3.9.0
[v3.8.0]: https://github.com/kennethreitz/responder/compare/v3.7.0..v3.8.0
[v3.7.0]: https://github.com/kennethreitz/responder/compare/v3.6.2..v3.7.0
[v3.6.2]: https://github.com/kennethreitz/responder/compare/v3.6.1..v3.6.2
[v3.6.1]: https://github.com/kennethreitz/responder/compare/v3.6.0..v3.6.1
[v3.6.0]: https://github.com/kennethreitz/responder/compare/v3.5.0..v3.6.0
[v3.5.0]: https://github.com/kennethreitz/responder/compare/v3.4.0..v3.5.0
[v3.4.0]: https://github.com/kennethreitz/responder/compare/v3.3.0..v3.4.0
[v3.3.0]: https://github.com/kennethreitz/responder/compare/v3.2.0..v3.3.0
[v3.2.0]: https://github.com/kennethreitz/responder/compare/v3.0.0..v3.2.0
[v3.0.0]: https://github.com/kennethreitz/responder/compare/v2.0.5..v3.0.0
[v2.0.5]: https://github.com/kennethreitz/responder/compare/v2.0.4..v2.0.5
[v2.0.4]: https://github.com/kennethreitz/responder/compare/v2.0.3..v2.0.4
[v2.0.3]: https://github.com/kennethreitz/responder/compare/v2.0.2..v2.0.3
[v2.0.2]: https://github.com/kennethreitz/responder/compare/v2.0.1..v2.0.2
[v2.0.1]: https://github.com/kennethreitz/responder/compare/v2.0.0..v2.0.1
[v2.0.0]: https://github.com/kennethreitz/responder/compare/v1.3.2..v2.0.0
[v1.3.2]: https://github.com/kennethreitz/responder/compare/v1.3.1..v1.3.2
[v1.3.1]: https://github.com/kennethreitz/responder/compare/v1.3.0..v1.3.1
[v1.3.0]: https://github.com/kennethreitz/responder/compare/v1.2.0..v1.3.0
[v1.2.0]: https://github.com/kennethreitz/responder/compare/v1.1.3..v1.2.0
[v1.1.3]: https://github.com/kennethreitz/responder/compare/v1.1.2..v1.1.3
[v1.1.2]: https://github.com/kennethreitz/responder/compare/v1.1.1..v1.1.2
[v1.1.1]: https://github.com/kennethreitz/responder/compare/v1.1.0..v1.1.1
[v1.1.0]: https://github.com/kennethreitz/responder/compare/v1.0.5..v1.1.0
[v1.0.5]: https://github.com/kennethreitz/responder/compare/v1.0.4..v1.0.5
[v1.0.4]: https://github.com/kennethreitz/responder/compare/v1.0.3..v1.0.4
[v1.0.3]: https://github.com/kennethreitz/responder/compare/v1.0.2..v1.0.3
[v1.0.2]: https://github.com/kennethreitz/responder/compare/v1.0.1..v1.0.2
[v1.0.1]: https://github.com/kennethreitz/responder/compare/v1.0.0..v1.0.1
[v1.0.0]: https://github.com/kennethreitz/responder/compare/v0.3.3..v1.0.0
[v0.3.3]: https://github.com/kennethreitz/responder/compare/v0.3.2..v0.3.3
[v0.3.2]: https://github.com/kennethreitz/responder/compare/v0.3.1..v0.3.2
[v0.3.1]: https://github.com/kennethreitz/responder/compare/v0.3.0..v0.3.1
[v0.3.0]: https://github.com/kennethreitz/responder/compare/v0.2.3..v0.3.0
[v0.2.3]: https://github.com/kennethreitz/responder/compare/v0.2.2..v0.2.3
[v0.2.2]: https://github.com/kennethreitz/responder/compare/v0.2.1..v0.2.2
[v0.2.1]: https://github.com/kennethreitz/responder/compare/v0.2.0..v0.2.1
[v0.2.0]: https://github.com/kennethreitz/responder/compare/v0.1.6..v0.2.0
[v0.1.6]: https://github.com/kennethreitz/responder/compare/v0.1.5..v0.1.6
[v0.1.5]: https://github.com/kennethreitz/responder/compare/v0.1.4..v0.1.5
[v0.1.4]: https://github.com/kennethreitz/responder/compare/v0.1.3..v0.1.4
[v0.1.3]: https://github.com/kennethreitz/responder/compare/v0.1.2..v0.1.3
[v0.1.2]: https://github.com/kennethreitz/responder/compare/v0.1.1..v0.1.2
[v0.1.1]: https://github.com/kennethreitz/responder/compare/v0.1.0..v0.1.1
[v0.1.0]: https://github.com/kennethreitz/responder/compare/v0.0.10..v0.1.0
[v0.0.10]: https://github.com/kennethreitz/responder/compare/v0.0.9..v0.0.10
[v0.0.9]: https://github.com/kennethreitz/responder/compare/v0.0.8..v0.0.9
[v0.0.8]: https://github.com/kennethreitz/responder/compare/v0.0.7..v0.0.8
[v0.0.7]: https://github.com/kennethreitz/responder/compare/v0.0.6..v0.0.7
[v0.0.6]: https://github.com/kennethreitz/responder/compare/v0.0.5..v0.0.6
[v0.0.5]: https://github.com/kennethreitz/responder/compare/v0.0.4..v0.0.5
[v0.0.4]: https://github.com/kennethreitz/responder/compare/v0.0.3..v0.0.4
[v0.0.3]: https://github.com/kennethreitz/responder/compare/v0.0.2..v0.0.3
[v0.0.2]: https://github.com/kennethreitz/responder/compare/v0.0.1..v0.0.2
[v0.0.1]: https://github.com/kennethreitz/responder/compare/v0.0.0..v0.0.1
