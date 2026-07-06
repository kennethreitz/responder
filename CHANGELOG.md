# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Opt-in CSRF protection: `API(csrf=True)` requires unsafe requests
  (`POST`/`PUT`/`PATCH`/`DELETE`) to present the session-bound token from
  `req.csrf_token` in an `X-CSRF-Token` header or `csrf_token` form field
  (`{{ req.csrf_input }}` renders the hidden input for templates), rejecting
  the rest with a `403`. Per-route overrides via
  `@api.route(..., csrf=False)` (webhook receivers) or `csrf=True` (protect a
  single route). The token check shares the request's single streaming form
  parse. Off by default; requires sessions.
- Proxy-headers support: `API(trust_proxy_headers=True)` now installs a
  middleware honoring RFC 7239 `Forwarded` and
  `X-Forwarded-Proto`/`X-Forwarded-Host`/`X-Forwarded-For`/`X-Real-IP`,
  rewriting the request's scheme, host, and client address to what the
  original client sent — HTTPS detection, redirects, URL building,
  trusted-host validation, and logged client IPs are then correct behind
  nginx/Caddy/load balancers.
- Streaming multipart uploads: multipart bodies now parse incrementally off
  the wire, spooling file parts to temporary files (rolling to disk past
  ~1 MB) instead of buffering the whole body in RAM. `File()` markers,
  `req.media("files")`, and `req.media("form")` all share the single
  streaming parse, and `max_request_size` is enforced chunk-by-chunk as the
  body arrives — uploads are bounded by disk, not memory.

### Changed

- `trust_proxy_headers=True` previously only affected the client IP recorded
  by `enable_logging`; it now rewrites the connection's scheme, host, and
  client address for the whole stack (see Added). Apps that enabled it purely
  for logging get the same logged IPs, plus correct scheme/host handling.
- `max_request_size` now defaults to 100 MiB instead of unlimited; pass
  `API(max_request_size=None)` for the previous behavior. Oversized bodies
  get a `413` as before.
- Generated OpenAPI operations now document Responder's operational responses:
  CSRF-protected unsafe routes get `403`, rate-limited routes get `429` (and
  fail-closed backend `503`), and request-size `413` is emitted only when a
  body cap is active. Rate limiter `429`/`503` responses now honor
  `API(problem_details=True)` and keep the legacy `{"error": ...}` JSON shape
  when `problem_details=False`.
- Because multipart parsing streams, the raw body is consumed by the parse:
  `await req.content` after `media("form"/"files")` raises a clear
  `RuntimeError` (await `req.content` first to keep the buffered, replayable
  8.x behavior), and handlers using `File()`/`Form()` markers no longer have
  the raw multipart body available (matching Starlette/FastAPI). Multipart
  text fields read via `media("form")` now share the streaming parser's
  limits (1000 parts, 1 MB per text field, `400` beyond them) and lossily
  decode invalid UTF-8 instead of silently dropping the field.
- Prepared the v9 behavior defaults: explicit `port=` now wins over a
  conflicting `PORT` environment variable, bare `add_route()` calls require an
  explicit endpoint by default, `Decimal` values serialize as strings by
  default, and GraphQL partial-data responses default to HTTP `200`. The
  explicit compatibility switches for these legacy behaviors remain available
  without deprecation warnings while apps migrate.

### Removed

- Removed the deprecated `API.session()` accessor. Use `api.requests` or
  `api.test_client(...)` instead.

## [v8.3.0] - 2026-07-05

### Added

- Added `api.test_client(...)`, a configurable form of `api.requests` for
  custom `base_url` values and Starlette `TestClient` options such as
  `raise_server_exceptions=False`, without relying on deprecated
  `api.session()`.
- Added v9 compatibility opt-ins: `API(json_decimal="string")` serializes
  `Decimal` values as precision-preserving strings, and
  `api.graphql(..., partial_data_status=200)` returns GraphQL partial-data
  responses with HTTP `200` per the GraphQL-over-HTTP spec. Apps can also pass
  `API(implicit_static_fallback=False)` to disable bare `add_route()` static
  fallbacks early, and `port_precedence="explicit"` to `serve()` / `run()` so
  an explicit `port=` wins over a conflicting `PORT` environment variable.
- Added `head()` and `options()` shortcut decorators on `API`, `RouteGroup`,
  and standalone `Router` instances.

### Changed

- Declared the Starlette test-client dependencies explicitly (`httpx` and
  `httpx2`), raised the Uvicorn and Granian dependency floors to current
  compatible releases, and allowed Sphinx 9 for documentation builds.
- Removed the PyPy classifier and remaining PyPy-specific test-extra guidance
  now that future CI runs target CPython only.

## [v8.2.3] - 2026-07-05

### Fixed

- `RedisSessionBackend` and `AsyncRedisSessionBackend` now write sessions with
  Redis `SET` plus expiry (`EX`) when the client supports it, avoiding the
  `setex` deprecation warning emitted by current redis-py/fakeredis while
  preserving a fallback for older or custom clients that only expose `setex`.

### Documentation

- Added a runnable examples guide, surfaced it in the sidebar navigation, and
  linked the existing v6 migration guide from the project navigation.
- Updated the docs Makefile so `make html` uses the declared documentation
  dependencies through `uv run --extra docs sphinx-build`.

## [v8.2.2] - 2026-07-03

### Fixed

- `RateLimiter.limit` now propagates the decorated handler's return value, so
  return-value-style handlers (`return {...}`, `return text`, or
  `return data, status[, headers]`) compose with the decorator. Previously the
  wrapper discarded the return value in both the sync and async branches, so
  any `@limiter.limit` handler that returned data instead of mutating `resp`
  produced an empty `200` response. Mutation-style handlers were unaffected,
  which is why it went unnoticed. The over-limit path is unchanged: a request
  past the limit still gets a `429` with `Retry-After` and the handler is not
  invoked.

## [v8.2.1] - 2026-07-03

A security patch closing two response-injection gaps found by an adversarial
sweep over the non-auth framework surface (the auth extension was hardened in
8.2.0). Both fixes are defense-in-depth and change no legitimate behavior.

### Security

- Response header values are now stripped of CR, LF, and NUL before they reach
  the ASGI layer, closing an HTTP response-splitting / header-injection vector.
  A user-controlled value written to a header — most commonly a redirect target
  via `Response.redirect(location)` or `Response.created(location=...)`, but also
  any `resp.headers[...] = ...` assignment — could previously carry a bare
  `\r\n` onto the wire, letting an attacker inject additional response headers
  (e.g. `Set-Cookie`) or split the response on ASGI servers/proxies that don't
  reject embedded control bytes. The framework already scrubbed CR/LF from
  `Content-Disposition` and Server-Sent Events fields; this extends the same
  discipline to every response header.
- The access-log middleware (`responder.ext.logging`) now strips control
  characters (C0 range and DEL) from the request method and path before logging
  them. Because ASGI servers percent-decode `scope["path"]`, a request to a
  path like `/x%0d%0a[INFO]...` previously wrote a raw CR/LF into the log line,
  letting an attacker forge log records in a plaintext log sink. Spaces and
  non-ASCII in a decoded path are preserved — only control bytes are removed —
  mirroring the validation already applied to inbound `X-Request-ID`.

## [v8.2.0] - 2026-07-03

A security-focused release hardening the JWT/OAuth2 authentication introduced
in 8.1.0. A dedicated adversarial pass over the new auth code surfaced nine
issues (all in `responder.ext.auth`); every fix ships with a regression test.

### Security

- `JWTAuth` rejects a plaintext (`http`) `jwks_url` at construction unless it
  targets loopback (`localhost`/`127.0.0.1`/`[::1]`) or `allow_insecure_jwks=True`
  is passed. A man-in-the-middle on a plaintext JWKS fetch could serve
  attacker-controlled signing keys — a full authentication bypass.
- `JWTAuth` no longer lets an attacker-controlled, unknown `kid` amplify into
  unbounded blocking JWKS refetches. The JWKS client now caches keys, and a
  missing `kid` triggers at most one network refresh per short cooldown window
  (otherwise rejecting with `401` and no fetch), closing an unauthenticated
  denial-of-service vector. The JWKS cache is lock-guarded for the threadpool.
- `JWTAuth` rejects tokens without an `exp` claim by default (new
  `require_exp=True`); previously an expiry-less token was accepted
  indefinitely. See the note under Changed to restore the old behavior.
- `OAuth2Auth` no longer mutates token-introspection principals in place, and a
  previously-stamped `scopes` value no longer short-circuits a fresh
  introspection result — fixing privilege retention where a token that the
  authorization server had downscoped kept its old, broader scopes.

### Changed

- `JWTAuth(require_exp=...)` defaults to `True`, so tokens without an `exp`
  claim are rejected. This is stricter than 8.1.0, which accepted them. If you
  intentionally issue non-expiring JWTs, pass `require_exp=False`.

### Fixed

- `JWTAuth`: a JWKS-configured scheme no longer raises an uncaught `TypeError`
  (HTTP 500) when sent an `HS256` token whose `kid` resolves to a public key —
  the mismatch now rejects with `401`.
- `JWTAuth`: `audience=None` now actually disables `aud` verification, so tokens
  carrying an `aud` claim (virtually every OIDC access token) are accepted
  instead of being rejected with `401`. When `audience` is set, it is still
  enforced.
- `JWTAuth`/`OAuth2Auth`: the `scope` and `scp` claims are now unioned rather
  than the first-present one winning, so an Azure AD / Auth0 token that carries
  its granted scopes in `scp` (with an empty `scope`) is no longer denied `403`.
- OAuth2 flows: constructing a flow with an empty required URL
  (`authorizationUrl`/`tokenUrl`) now raises `ValueError` instead of silently
  emitting an OpenAPI security scheme that fails validation.

### Packaging

- The `orjson` and `fakeredis[lua]` test dependencies (compiled, without wheels
  on free-threaded CPython or PyPy) moved out of the `test` extra into a
  version-scoped CI step, so `pip install responder[test]` on free-threaded
  3.14 no longer fails trying to build them from source.

## [v8.1.0] - 2026-07-03

### Added

- `JWTAuth` in `responder.ext.auth`: `Authorization: Bearer` authentication
  with full JWT validation — signature (HS256 by default; RS256/ES256 and
  friends when `cryptography` is installed), `exp`/`nbf`/`iat` with a
  configurable `leeway`, and `audience`/`issuer` checks. Keys come from a
  static `secret` (HMAC secret or PEM public key) or a `jwks_url`, resolved by
  the token's `kid` header with cached key sets that refresh automatically on
  rotation (the JWKS fetch runs off the event loop). The validated claims
  become the injected principal — or pass `verify=` to map them onto your own
  user object — and the token's `scope`/`scp` claim is normalized into the
  scopes that feed `requires()`. Invalid tokens reject with `401` + a `Bearer`
  challenge, missing scopes with `403` + `insufficient_scope`. Requires the new
  `responder[jwt]` extra (PyJWT), imported lazily with a helpful error.
- `OAuth2Auth` and flow descriptions (`OAuth2AuthorizationCodeFlow`,
  `OAuth2ClientCredentialsFlow`, `OAuth2PasswordFlow`) in `responder.ext.auth`:
  the OpenAPI document emits a proper `type: oauth2` security scheme with flows
  and scopes, lighting up Swagger UI's Authorize button. At runtime it acts as
  a resource server — bearer tokens are validated locally through a `jwt=`
  `JWTAuth` or a `verify=` token-introspection callable. Convenience
  constructors `OAuth2Auth.authorization_code(...)`, `.client_credentials(...)`,
  and `.password(...)` cover the single-flow cases; both compose with
  `requires()`/`optional()`/`AuthPolicy` and group-level auth via
  `Router`/`include_router`.
- `responder.Problem` — a raisable RFC 9457 problem-details exception. Raise
  `Problem(409, "detail", title=..., type=..., instance=..., **extensions)`
  from any view, hook, or dependency to short-circuit with a full
  `application/problem+json` response, rendered through the same pipeline as
  framework errors (`problem_handler` enrichment, request IDs, the app's JSON
  encoder). `instance` defaults to the request path, and because it subclasses
  Starlette's `HTTPException`, existing handlers and middleware treat it like
  `abort()`.
- `abort()` now accepts the RFC 9457 members — `title=`, `type=`, `instance=`,
  `errors=`, and arbitrary extension keywords — raising a `Problem` that
  carries them into the payload. Plain `abort(status, detail=..., headers=...)`
  still raises a regular `HTTPException`, unchanged.
- `@api.middleware("http")` — a decorator for function-style HTTP middleware:
  `async def mw(request, call_next) -> response`, no ASGI class boilerplate.
  Synchronous functions run on a dedicated thread pool and receive a blocking
  `call_next`. Function middleware registers through the same stack as
  `add_middleware`, so the two compose: the most-recently-registered user
  middleware is outermost. Backed by the new public
  `responder.middleware.FunctionMiddleware` adapter; WebSocket and lifespan
  traffic pass through untouched.
- `req.headers.get_list("X-Forwarded-For")` returns every raw line of a
  repeated request header, in the order received — duplicate header lines are
  no longer silently collapsed. Single-value access is unchanged: last wins.
- Write-side HTTP preconditions (RFC 9110 §13): on state-changing methods,
  when `resp.etag` or `resp.last_modified` is set, `If-Match` (strong
  comparison, `*` supported), `If-Unmodified-Since` (only when `If-Match` is
  absent), and `If-None-Match` are evaluated, and a failing precondition
  answers `412 Precondition Failed`. Repeated header lines are folded per the
  RFC. Like the existing `304` handling, preconditions are checked only when
  the matching validator is set.
- `req.preferred_media_type(candidates)` — ranks candidate media types (or
  bare subtype tokens) by the q-value of the most specific matching `Accept`
  range and returns the client's preferred one, or `None` when it accepts
  none. Ties, and requests without an `Accept` header, keep the candidate
  order.
- Pydantic body-model injection for class-based views:
  `on_post(self, req, resp, *, item: Item)` receives the parsed, validated
  body exactly like function views (automatic `422` on invalid input). The
  body is parsed once per request, so `on_request` and a method handler
  declaring the same model share one validated instance.
- `Query()`, `Header()`, `Cookie()`, and `Path()` markers now resolve on
  WebSocket handlers from the handshake request (query string, headers,
  cookies), with the same Pydantic coercion as HTTP views. A missing required
  value or failed coercion closes the socket with `1008` (policy violation)
  before the handler runs.
- Bind an entire form to a Pydantic model: a parameter annotated with a
  `BaseModel` subclass and a `Form(...)` default validates the parsed
  urlencoded/multipart form into the model in one shot — repeated keys collect
  into list fields, uploads bind to `UploadFile` fields, aliases are honored,
  and failures return the same `422` payload as JSON body models with
  `["form", <field>]` error locations. The OpenAPI schema documents the model
  as the `multipart/form-data`/`application/x-www-form-urlencoded` requestBody.
- `RateLimiter` accepts `key=` — a callable `req -> str` naming the bucket a
  request counts against (an API key, a user id) instead of the client IP
  (still the default and the fallback) — and `fail_open=`: when the backend
  errors out (e.g. Redis is unreachable), `fail_open=True` logs a warning and
  lets the request through, while the default `False` answers `503` instead of
  an unhandled 500. Every response now carries `X-RateLimit-Reset`; the backend
  `hit()`/`ahit()` contract grew a third `reset_after` element, with 2-tuple
  third-party backends still accepted (the header is omitted).
- `MetricsCollector` accepts `buckets=` — ascending histogram bucket bounds —
  exposed as `API(metrics_buckets=...)`, and `/metrics` now exports a
  `responder_requests_in_flight` gauge.
- `responder.ext.pagination.set_pagination_headers(req, resp, page)` emits the
  RFC 8288 `Link` header (`rel="first"`/`"prev"`/`"next"`/`"last"`, built from
  the request URL with all other query parameters preserved) plus
  `X-Total-Count`, alongside the existing `Page` body envelope.
- CLI: `responder run` accepts `--host=<addr>`, `--port=<n>`, and
  `--server=<name>` (`uvicorn` or `granian`) to control binding and backend,
  plus `--reload` for uvicorn auto-reload (requires a `module:attr` target).
- Optional orjson JSON backend: when orjson is installed
  (`pip install "responder[orjson]"`), JSON responses encode via orjson —
  typically several times faster, with less event-loop blocking. Custom
  `API(encoder=...)` hooks keep working, and `json_ensure_ascii=True` stays on
  the stdlib (orjson is UTF-8-only). Decoding continues to use the stdlib so
  arbitrary-precision integers stay exact. Encoded output differs only in
  whitespace, except float `nan`/`inf` serialize as `null` instead of the
  non-standard `NaN`/`Infinity`.
- `responder.testing.AsyncTestClient`: the async mirror of `api.requests` — an
  `httpx.AsyncClient` dispatching in-process; `async with` runs the app's
  lifespan. Backed by `ASGIStreamingTransport`, which streams response bodies
  live, so endless SSE streams can be read and closed mid-flight.
- SSE test helpers in `responder.testing`: `parse_sse(text)`,
  `iter_sse(response)`, and `collect_sse(response)` parse `text/event-stream`
  bodies into `SSEEvent` objects (`data`/`event`/`id`/`retry`, plus `.json()`),
  skipping heartbeat comments — the client-side mirror of `resp.sse`.
- Test coverage for the Redis session and rate-limit backends against
  `fakeredis` (sync and async, including the atomic INCR+EXPIRE Lua script),
  covering TTL/expiry, key prefixes, middleware round-trips, and outage
  behavior.
- `status_code=` route option: `@api.route("/items", methods=["POST"],
  status_code=201)` pre-seeds `resp.status_code` (an explicit assignment in
  the view still wins) and documents the success response under that status in
  the OpenAPI operation instead of a hardcoded `200`. Works with the method
  sugar, `api.group()`, and standalone `responder.Router` declarations.

### Changed

- Content negotiation honors the client's q-ranked `Accept` preference order
  when a response could serialize to multiple registered formats, instead of
  always serving the first registered format:
  `Accept: application/yaml;q=0.9, application/json;q=0.1` now gets YAML where
  it previously got JSON. Behavior changes only when a client explicitly ranks
  formats — spec-correct per RFC 9110 §12.5.1; without an `Accept` header (or
  when everything ties) JSON remains the default.
- Class-based views now auto-answer `OPTIONS`. A CBV that defines neither
  `on_options` nor `on_request` responds to `OPTIONS` with `200` and an
  `Allow` header listing the methods it serves, instead of `405` — the same
  treatment method-restricted function routes already got (RFC 9110 §9.3.7).
  The `Allow` header on a CBV's `405` response now includes `OPTIONS` too,
  keeping the advertised methods consistent between the two paths.
- Class-based views now reject unsupported HTTP methods with `405` *before*
  validating typed parameters, so a request using a method the CBV does not
  serve gets `405` (with `Allow`) rather than a `422` from a handler that would
  never run — matching how function routes have always ordered method dispatch
  and parameter validation. Supported methods still validate and return `422`
  on bad input.
- `RequestIDMiddleware` and `LoggingMiddleware` now share one request-ID
  policy: an inbound `X-Request-ID` is honored when it is at most 128 printable
  ASCII characters (`\x21`–`\x7e`, so namespaced ids like `gateway:abc123` and
  base64-flavored ids pass, while control characters and oversized values are
  replaced to prevent header/log injection and bloat), and both mint full
  UUID4s
  otherwise — `LoggingMiddleware` previously minted 8-hex-char ids (32 bits of
  entropy), so a busy fleet risked collisions and flipping `enable_logging`
  changed the ID format. `setup_logging` is now exported in `__all__`.
- Coverage is now enforced (`fail_under = 80`, previously 0) and measures only
  the `responder` package, so generated clientgen artifacts and `examples/*.py`
  no longer pollute the report.
- The generated OpenAPI document is cached instead of rebuilt on every request
  to the schema route or docs page; registering a route, schema, or security
  scheme invalidates the cache.

### Fixed

- Session values that work with `MemorySessionBackend` — `datetime`, `date`,
  `time`, `Decimal`, `UUID`, `set`, `frozenset`, `bytes` — no longer crash the
  Redis session backends with a hard-to-debug 500: the new default
  `JSONSessionSerializer` round-trips those types (and refuses user data that
  collides with its reserved type tag), and a `serializer=` parameter accepts
  any custom `dumps`/`loads` codec. The storable-values contract is documented
  for all backends.
- Prometheus `/metrics` output escapes backslashes, double quotes, and
  newlines in label values per the exposition format, so a route pattern
  containing them can no longer break the whole scrape. The `RateLimiter`
  docstring no longer claims "token bucket" (the memory backend is a sliding
  window; the Redis backends are fixed-window).
- Registering two *different* security schemes under the same name now raises
  instead of silently overwriting (idempotent re-registration of the same
  scheme, which routing performs per route, is still fine).
- CLI: bare `responder` with no subcommand prints the usage summary and exits
  non-zero instead of silently exiting `0`.

### Deprecated

- `API.session()` — use the `api.requests` property. Emits a
  `DeprecationWarning`; removal in 9.0.
- The `PORT` environment variable overriding an explicitly passed `port=` in
  `serve()`/`run()`. Warns only when both are set and differ; the environment
  variable still wins until 9.0, when explicit `port=` will take precedence.
- Calling `add_route()` without an endpoint (implicitly registering a
  static-fallback route). Pass an endpoint explicitly (with `default=True` for
  a catch-all); the implicit behavior is removed in 9.0.
- Lossy `decimal.Decimal`-to-float JSON serialization. Warns once per process
  when a bare `Decimal` reaches the built-in encoder; 9.0 will serialize
  `Decimal` as strings. Convert explicitly or pass a custom `encoder=`.
- GraphQL responses returning HTTP 400 when the result contains partial data
  alongside errors. Warns once per process; 9.0 will return 200 for such
  responses per the GraphQL-over-HTTP spec (no-data error responses keep 400).

## [v8.0.2] - 2026-07-03

### Fixed

- `ResponderServer`'s private process-termination method no longer shadows
  `threading.Thread._stop()`. On Python ≤ 3.12 and PyPy, `Thread.join()` calls
  `self._stop()` internally, so joining a `ResponderServer` invoked the
  process-termination logic (marking the server as stopping and skipping the
  Thread's own bookkeeping) instead of just waiting. Python 3.13+ was
  unaffected. Latent since the class was introduced; surfaced by the 8.0.1
  signal-handler restore tests.

## [v8.0.1] - 2026-07-02

### Added

- A "Migrating to v8" guide in the documentation, covering every 8.0 breaking
  change with before/after examples — implicit `static/` removal,
  `RouteNotFoundError` and percent-encoding in `url_for`, the 307 redirect
  default, text-response charsets, `application/yaml`, empty-YAML-body 400s,
  and the new dependency declarations and floors — plus a pointer to the
  standalone-routers guide.

### Fixed

- Trailing-slash redirects no longer crash on non-latin-1 paths: the
  `Location` header is percent-encoded (`GET /日本/` now answers `307` instead
  of a 500 from a `UnicodeEncodeError`), and the redirect now matches the
  alternate path regardless of request method — `POST /page/` redirects to
  `/page` just like GET does, instead of falling through to a 404.
- A sync view that outlives `request_timeout` can no longer corrupt the
  `504 Gateway Timeout` response: the timeout reply is built on a fresh
  `Response`, so the abandoned thread's late writes land on an inert object.
  Headers and cookies set by completed `before_request` hooks (request IDs,
  CORS) are carried onto the 504; stale body-framing headers are not.
- The route-resolution cache evicts its least-recently-used entry when full
  instead of clearing all 1024 entries wholesale, so high-cardinality
  parameterized paths no longer periodically wipe the hot static entries.
- Route registration errors are real exceptions instead of `assert` statements
  (which vanish under `python -O`): a route path without a leading `/`, an
  unknown path convertor, and an unknown event type all raise `ValueError`
  naming the offender, and a duplicate path parameter (`/x/{id}/{id}`) is
  caught up front with a clear message instead of a bare `re.error`.
- The legacy `on_event("shutdown")` lifespan path now mirrors the `lifespan=`
  path when a shutdown handler raises: app-scoped dependency teardowns still
  run and `lifespan.shutdown.failed` is sent, instead of both being skipped.
- Class-based-view 405 responses now include the `Allow` header required by
  RFC 9110 §15.5.6, listing the methods the class actually serves via `on_*`
  handlers (plus implicit `HEAD` when `on_get` exists).
- `QueryDict` item assignment and `update()` no longer corrupt single-value
  reads: assigning a scalar (`qd["x"] = "hello"`) previously stored a bare
  string, so reading it back returned the last character. Scalars are stored
  as one-element lists, and `update()` from another `QueryDict` merges the
  full multi-value lists, matching 8.0.0 semantics.
- `req.accepts()` honors media-range specificity per RFC 9110 §12.5.1: an
  explicit `q=0` range now excludes the type even when a broader range
  (`*/*` or `type/*`) would match — and a `q=0` wildcard of a *different*
  top-level type no longer vetoes unrelated bare-token lookups.
- `req.apparent_encoding` recognizes valid UTF-8 bodies with a cheap strict
  decode and runs chardet detection in a worker thread, so a large body
  without a `charset=` parameter no longer stalls the event loop.
- `resp.stream_file()` and `resp.download()` now send `Content-Length`
  (computed exactly for full-body, single-range, and multipart byte-range
  responses). Replacing the body afterwards (`resp.text`, `resp.problem()`,
  `resp.created()`) clears the stale `Content-Length`/`Content-Range`/
  `Accept-Ranges` so the response cannot be misframed.
- `HEAD` requests against `resp.file()` no longer read the entire file in the
  threadpool just to discard the body — the headers (including the
  stat-derived `Content-Length`) are sent without touching the file contents.
- `resp.problem()` serializes extension members through the same type-aware
  JSON encoder as `resp.media` (datetimes, UUIDs, Decimals, sets, dataclasses,
  Pydantic models, and any custom `API(encoder=...)`) instead of bare
  `json.dumps`, so `resp.problem(409, occurred_at=datetime.now())` no longer
  turns into a 500.
- `resp.stream()` and `resp.sse()` raise a descriptive `TypeError` when given
  a non-async-generator function, instead of a message-less `assert` that
  vanished under `python -O`.
- OpenAPI parameter schemas for enum and nested Pydantic types no longer emit
  dangling `$ref`s or inline `$defs`: referenced definitions are hoisted into
  `components/schemas` (same-named models from different modules get distinct
  component names) and the generated document passes `openapi-spec-validator`.
- Generated clients (Python, JavaScript, TypeScript, Ruby, PHP) now actually
  send the header and cookie parameters they accept.
- Generated clients send `Form()`/`File()` request bodies as
  `application/x-www-form-urlencoded` or `multipart/form-data` per the
  documented requestBody media type, instead of JSON the server rejected with
  422 — in every supported client language. Ruby multipart parts carry proper
  filenames (with a `[filename, content]` pair form), and the PHP client
  distinguishes file specs from plain list values.
- WebSocket routes are no longer documented as HTTP GET operations in the
  OpenAPI schema.
- `API(docs_route=...)` without `openapi=` now enables schema generation
  (defaulting to OpenAPI 3.1.0) as documented, instead of serving a docs page
  pointing at a 404 schema. An app that already registered its own route at
  the schema path keeps it — the implied route is skipped with a warning.
- Required JSON and form request bodies are marked `required: true` in the
  OpenAPI schema, so generated clients require the body instead of defaulting
  it to `None` and always failing at runtime.
- The client generators emit correct code on parameter-name collisions
  (`user-id` vs `user_id`, or parameters named `body`/`path`/`quote`):
  deduplication and reserved-identifier handling are applied consistently to
  signatures, query/header/cookie maps, and path interpolation in all four
  generators.
- Optional sequence query parameters (`tags: list[int] | None = Query(None)`)
  now bind repeated values as a list instead of returning 422 — marker
  sequence detection unwraps `Optional`/`Union` annotations.
- An `async def` `problem_handler` is now applied on every error path — the
  negotiated 404/500 path, route-level 422/504/405 responses, and
  `resp.problem()` — instead of being silently dropped with a logged warning
  and a never-awaited coroutine.
- `SecurityHeadersMiddleware` no longer crashes every response when a header
  value is `None` — a `None` value in `headers=` now removes that default
  header (e.g. `headers={"x-frame-options": None}` for embeddable apps).
- `responder run app.py` registers the loaded module in `sys.modules` before
  executing it, so dataclasses, pickling, and `typing.get_type_hints` inside
  loaded apps work. A pre-existing module with the same name is never
  clobbered (the target gets a unique key), and only the added entry is
  removed if execution fails.
- Multipart part bodies accumulate in a `bytearray` during parsing, avoiding
  quadratic copying when the parser delivers a part in many chunks.
- `ResponderServer` no longer crashes when constructed off the main thread or
  when `is_running()`/`stop()` are called before `start()`. Signal handlers
  are installed by `start()` (main thread only) and restored on `stop()`,
  correctly even when multiple servers are stopped out of order.
- `API.path_matches_route` accepts the plain path string its docstring
  documents — for HTTP and WebSocket routes — instead of raising `TypeError`;
  ASGI scope mappings are still accepted.
- `API.session(base_url=...)` rebuilds the cached test client when called
  with a different `base_url` instead of silently returning the previous
  client bound to the old address.

## [v8.0.0] - 2026-07-01

### Added

- Standalone router composition: `responder.Router` records route declarations
  without an `API` instance — like Flask blueprints or FastAPI's `APIRouter` —
  and `api.include_router(router, prefix=...)` attaches them. Routers nest
  (prefixes compose, tags merge, dependencies concatenate), carry group-level
  `tags`/`dependencies`/`auth` defaults that individual routes may override,
  scope their `before_request` hooks to the mounted prefix, and can be included
  at more than one prefix. Declarations are replayed through `api.route()`, so
  auth inheritance, `Depends` guards, and OpenAPI metadata work exactly as if
  declared on the API directly. Including a view with auth/tags/dependencies
  that conflict with an earlier registration of the same function raises
  `ValueError` instead of silently rewriting the earlier route (an
  access-control hazard otherwise). `api.group()` remains for quick same-file
  prefix grouping.

### Changed

- Text responses now declare their charset the standard way —
  `Content-Type: text/...; charset=<encoding>` — and `resp.encoding` is honored
  when encoding `str` bodies for all text types (previously `resp.html` ignored
  it and str bodies fell through to a UTF-8 encode, producing mojibake for e.g.
  `latin-1`). The charset parameter is only added when the framework actually
  encodes a `str` body; raw `bytes` bodies (e.g. `resp.file()`) keep their bare
  media type. The nonstandard `Encoding` response header is no longer sent.
- `resp.redirect()` (and the `api.redirect()` helper) now default to a
  method-preserving `307 Temporary Redirect` instead of `301 Moved Permanently`,
  which browsers cache indefinitely and legacy clients rewrite POST→GET. Pass
  `permanent=True` for a `308 Permanent Redirect`; an explicit `status_code=`
  still takes precedence.
- YAML responses are served as `application/yaml` (the RFC 9512 registered
  media type) instead of the legacy `application/x-yaml`; the OpenAPI
  `/schema.yml` route follows suit. Inbound requests are unaffected — bodies
  sent with either media type still parse as YAML, and
  `Accept: application/x-yaml` still negotiates a YAML response.
- An empty or whitespace-only YAML request body now raises `400 Bad Request`
  from `req.media()`, matching the JSON format (previously `yaml.safe_load`
  silently returned `None`). An explicit YAML `null` document still parses to
  `None`.
- `url_for` now raises `RouteNotFoundError` (a `LookupError` subclass,
  importable as `responder.RouteNotFoundError`) for an unknown endpoint or
  route name instead of returning `None` (which rendered a literal "None" in
  templates), and generated URLs percent-encode parameter values —
  `url_for(view, name="a b")` yields `/file/a%20b`; `{param:path}` segments
  keep their slashes.
- Requests into a WSGI mount no longer build a fresh `a2wsgi.WSGIMiddleware`
  (and its per-instance `ThreadPoolExecutor`) on every request; the app is
  wrapped once, at mount time.
- `API()` no longer creates `static_dir` implicitly. Merely instantiating
  `API()` used to `mkdir` an empty `static/` into the working directory (and
  failed on read-only filesystems); the default `static/` is now mounted only
  when the directory already exists, and an explicitly passed `static_dir` that
  doesn't exist raises `FileNotFoundError` at construction. `static_dir=None`
  still disables static serving, and the docstrings no longer claim
  `static_dir`/`templates_dir` are "created for you".

### Fixed

- `Request.cookies` no longer silently drops cookies: the `Cookie` header is
  now parsed with Starlette's tolerant parser instead of
  `http.cookies.SimpleCookie`, which aborted at the first nonconforming token
  (e.g. `b[]=2`) and discarded that cookie and every one after it.
- `Response.headers` is now a case-insensitive, case-preserving mapping, so a
  handler setting `resp.headers['content-type']` replaces the framework's
  `Content-Type` instead of emitting both headers on the wire (and
  `no_content()` no longer needs to pop both spellings). `CaseInsensitiveDict`
  construction from a mapping, `|=`/`|`, `.copy()`, and `.fromkeys()` are also
  case-insensitive now — each previously bypassed the lowercasing — iteration
  preserves the casing keys were set with, and instances survive `pickle`,
  `copy.copy`, and `copy.deepcopy` round-trips.
- Background tasks now support async functions: `@api.background.task` and
  `api.background.run` drive an `async def` to completion via `asyncio.run` on
  the worker thread (a `functools.partial` wrapping one works too). Previously
  an async task silently produced a never-awaited coroutine and the job never
  ran. Background worker threads are also now named `responder-background-*`
  for easier debugging. Sync task behavior is unchanged.
- Assigning `templates.context = {...}` no longer wipes Jinja2's built-in
  globals (`range`, `namespace`, `cycler`, `joiner`, `lipsum`, `dict`) —
  previously any template using `range()` after a context assignment raised
  `UndefinedError`. The setter now updates the globals dict in place (keeping
  the sync and async environments sharing one globals object), and reassigning
  the context restores any built-in a previous assignment had shadowed.
- The GraphQL view now executes queries with `schema.execute_async`, so
  `async def` resolvers (first-class in graphene 3) resolve correctly instead
  of failing with a "coroutine was never awaited" error, and resolvers can
  yield the event loop while they await. The query document is also parsed once
  and shared by the GET-mutation guard and introspection/depth validation
  instead of being re-parsed by each.
- `API(lifespan=...)` no longer silently disables `on_event` handlers: startup
  events run after the lifespan context manager is entered and shutdown events
  before it exits, so background-task draining (registered as a shutdown event)
  is no longer skipped. A raising lifespan `__aexit__` now still tears down
  app-scoped dependencies and reports `lifespan.shutdown.failed`.
- `add_route(default=True)` with a Responder `(req, resp)` view no longer
  crashes every unmatched request (the default endpoint was invoked ASGI-style,
  raising `TypeError` → 500). A view-style default is now dispatched through
  normal route semantics — before/after hooks, dependency injection, and
  response handling — with empty path params; ASGI-style defaults (including
  the built-in 404) are unchanged.
- `HEAD` requests to a class-based view that defines only `on_get` now fall
  back to `on_get` (with the response body stripped) instead of returning 405,
  matching the implicit HEAD support function views already had.
- A mount prefix with a trailing slash (`api.mount("/admin/", app)`) now
  matches subpaths; the prefix is normalized at mount time, and the
  empty-prefix root mount still catches everything.
- An unhandled exception in a WebSocket handler now closes the socket with code
  1011 (internal error) before re-raising, so clients can tell a server bug
  from a network drop instead of seeing an abnormal 1006 with no close frame.
- Two distinct dependency providers that share a `__name__` (two functions both
  named `get_db`, or nested inline lambda `Depends`) no longer trip a false
  `DependencyCycleError` — cycle detection now tracks provider identity,
  keeping names only for the error message.
- `url_for` no longer raises `AttributeError` when any registered route uses a
  callable-instance endpoint without a `__name__` (e.g. a mounted
  `GraphQLView`); the by-name fallback scan now skips such endpoints.
- `api.group(...).route(None)` now raises the usual "a route path is required"
  error instead of silently registering a literal `/prefixNone` path built from
  the f-string.
- `jinja2`, `pyyaml`, and `itsdangerous` are now declared as direct
  dependencies. Responder imports them directly (templates, YAML content
  negotiation, and cookie-based session middleware) but previously received
  them only transitively via the `starlette[full]` extra. `python-multipart`
  now carries a `>=0.0.12` floor (the first release that ships the
  `python_multipart` module name Responder imports), the `apispec` and
  `marshmallow` floors were raised to `>=6.6` and `>=3.20` to match what the
  OpenAPI extension is tested against, and the build system now requires
  `setuptools>=77`, the first version that supports the PEP 639 SPDX
  `license = "Apache-2.0"` expression (the previous `>=42` pin could not build
  the project).

### Security

- `resp.download()` sanitizes user-derived filenames in `Content-Disposition`:
  CR, LF, and NUL are stripped (they allowed header injection), `"` and `\` are
  backslash-escaped in the quoted-string (a quote could break out of
  `filename="..."`), and names containing characters outside the HTTP token set
  now also carry the RFC 5987 `filename*=` form (non-ASCII names keep the
  `filename*=`-only form).
- The in-memory `MemorySessionBackend` now bounds its store (LRU eviction,
  `max_keys`, default 100,000) and opportunistically sweeps expired entries on
  writes, so an attacker rotating session cookies — or plain abandoned
  sessions, previously deleted only on a `get()` with that exact ID — can no
  longer grow process memory without bound. Eviction removes earliest-expiry
  entries first, and operations are lock-guarded, as they run on threadpool
  workers.

## [v7.3.0] - 2026-07-01

### Added

- Inline `Depends(...)` can now nest inside a provider (sub-dependency chains),
  matching FastAPI. Previously only named `@api.dependency()` providers could
  chain; a provider with an inline `Depends` parameter raised
  `DependencyResolutionError`.
- A developer `Makefile`: bare `make` lists tasks (`test`, `lint`, `types`,
  `docs`, `build`, `check`, …), all run through `uv`. `make test` and
  `make docs` depend on `make uv-sync`.

### Changed

- `Request.accepts()` now honors `Accept` media ranges (`*/*`, `type/*`) and
  q-values (a `q=0` range is not acceptable) instead of substring-matching the
  raw header. An absent `Accept` accepts anything.
- Request body decoding honors the standard `Content-Type` `charset=` parameter,
  using chardet only when no charset is declared.
- `Request.media()` auto-detects MessagePack (`application/x-msgpack`) bodies.
- Dependency and request-body-model signature inspection is now memoized on the
  per-request path, and large `auto_etag` response bodies are hashed off the
  event loop.
- The project `Homepage` URL now points at the documentation site.

### Fixed

- Multipart form parsing no longer emits file parts (or their filenames) as
  phantom form fields, and correctly parses unquoted field names — it now uses a
  real Content-Disposition parser.
- `enable_logging` no longer drops a request whose `X-Request-ID` is not valid
  UTF-8; the header is decoded as latin-1, matching `RequestIDMiddleware`.
- The `responder.ext.query` descending sort keeps `None` values last, per its
  documented contract (it previously pushed them to the front).
- Mounted sub-app WSGI-vs-ASGI detection is decided from the call signature
  rather than the text of a raised `TypeError`, so a genuine `TypeError` inside a
  mounted ASGI app keeps its real traceback instead of being misclassified.
- A user `lifespan(app)` context manager now receives the `API` instance instead
  of `None`.
- `MetricsCollector` reads/writes are lock-guarded, so a concurrent `/metrics`
  scrape no longer risks `RuntimeError: dictionary changed size during
  iteration` and counts stay exact on free-threaded CPython.
- `Templates.render_async` uses a dedicated always-async Jinja environment
  instead of toggling `is_async` on a shared one, fixing a race between
  concurrent sync and async renders.

### Security

- The in-memory rate-limit `MemoryBackend` now bounds its key set (LRU eviction,
  `max_keys`), so a client rotating source IPs (or spoofing `X-Forwarded-For`)
  can no longer grow process memory without bound.
- The Server-Sent Events heartbeat queue is now bounded, restoring producer
  backpressure so a slow/stalled client can't make it buffer without limit.

## [v7.2.1] - 2026-07-01

### Security

- The GraphQL view now rejects mutations sent over HTTP `GET` (returns `405`
  with `Allow: POST`). `GET` must be safe/idempotent; running a mutation via
  `GET /graphql?query=mutation{...}` made it CSRF-able (a plain `<img src>`
  would trigger it with the victim's cookies) and cacheable. Query operations
  over `GET` are unaffected.

### Fixed

- The client generator now escapes path-parameter names when interpolating them
  into generated string literals (Python `repr`, JS via the JS escaper, Ruby via
  a new single-quoted escaper). A name containing a quote or backslash in an
  (untrusted) OpenAPI spec previously produced a client that failed to parse.

## [v7.2.0] - 2026-07-01

### Security

- Server-Sent Events frames can no longer be spoofed via caller-supplied field
  values. A `\r` or `\n` in an SSE `event`, `id`, `retry`, or comment field is
  now stripped (these are single-line fields; a line break would terminate the
  field and inject additional SSE lines/frames), and `data` splits on any line
  terminator (`\r\n`, `\r`, `\n`) so a lone `\r` can't inject a new field.

### Added

- `API(ws_idle_timeout=...)` closes a WebSocket that waits too long for the next
  inbound message (close code `1001`). The deadline resets on every message, so
  it bounds idle time between messages, not total connection lifetime. Defaults
  to `None` (unlimited), preserving existing behavior.

### Changed

- Tightened the mypy configuration: `no_implicit_optional`, `warn_unused_ignores`,
  and `warn_redundant_casts` are now on. Removed the blanket
  `implicit_optional = true`. Cleaned up the stale `# type: ignore` comments this
  surfaced.
- Completed type annotations across the codebase and enabled mypy
  `disallow_incomplete_defs`, so partially-annotated functions now fail type
  checking. Annotations only — no runtime behavior changed.
- Dropped the separate "Generated client syntax checks" CI job.

### Fixed

- Synchronous lifecycle handlers (`startup`/`shutdown` events) and synchronous
  exception handlers now run in a thread pool instead of directly on the event
  loop, so a blocking call in one no longer stalls the server.
- Content-Type matching is now case-insensitive, per RFC 7231. Requests sent
  with e.g. `Application/JSON` or `Multipart/Form-Data` are negotiated and
  parsed correctly instead of falling through to the wrong parser. Affects
  `Request.is_json`, `Request.media()` format auto-detection, and multipart
  form parsing (including an uppercased `Boundary` parameter name).
- `ResponderServer.__init__`'s `limit_max_requests` parameter was annotated
  `int` but defaulted to `None`; it is now `int | None`.

## [v7.1.3] - 2026-07-01

### Fixed

- `RedisBackend`/`AsyncRedisBackend` rate limiters now increment the counter
  and set its expiry in a single atomic server-side step (Lua `EVAL`). The
  previous separate `INCR` + `EXPIRE` calls could leave a key without a TTL if
  the process died between them, locking that client out until manual eviction.
- Background tasks submitted via `api.background` are now drained on application
  shutdown instead of being abandoned, via a registered shutdown handler that
  calls `BackgroundQueue.shutdown()`.

### Changed

- CI now runs `ruff check` and `mypy` on every push and pull request (new
  `Lint & Types` workflow), not just during the manual release guard.

## [v7.1.2] - 2026-06-30

### Added

- New example apps: `examples/todo.py`, `examples/fortunes.py`,
  `examples/shortlinks.py`, `examples/tarot.py`, and `examples/webhooks.py`.
- Modernized `examples/helloworld.py`, `lifespan.py`, `marimo_mount.py`,
  `rest_api.py`, `sse_stream.py`, `user.py`, and `websocket_chat.py`, with
  matching coverage in `tests/test_examples.py`.

### Fixed

- `RateLimiter` and `enable_logging`'s `LoggingMiddleware` keyed/recorded the
  client IP from the TCP peer only, which behind any reverse proxy is the
  proxy itself — every visitor shared one rate-limit bucket and access logs
  showed the proxy's address for every request. Both now accept
  `trust_proxy_headers=True` to read the real client from
  `X-Forwarded-For`/`X-Real-IP` instead (opt-in, since trusting those headers
  without a proxy in front lets a client spoof its own address).

## [v7.1.1] - 2026-06-30

### Added

- `examples/atelier.py` is now the canonical showcase app and contract fixture
  for auth policies, OpenAPI metadata, response helpers, problem details, and
  generated clients.

### Fixed

- Auth-injected `user`, `principal`, and `auth` parameters are no longer treated
  as request-body Pydantic models on routes with JSON bodies.
- Generated clients now keep scanning success responses until they find a JSON
  schema, instead of giving up when an earlier 2xx response has no body.

## [v7.1.0] - 2026-06-30

### Added

- `api.policy(name, auth)` creates named, reusable auth policy wrappers without
  changing the wrapped auth scheme's runtime or OpenAPI behavior.
- Route decorators now accept `responses=`, `examples=`, `response_examples=`,
  and `openapi_extra=` for nearby OpenAPI operation authoring that deep-merges
  with generated schemas and framework error responses.
- `resp.created(...)`, `resp.no_content()`, and `resp.problem(...)` provide
  focused helpers for common REST responses and manual problem-details payloads.
- A canonical contract fixture now validates a representative OpenAPI document
  and generated Python client against the same in-process app.
- `scripts/release.py` orchestrates the release guard, tagging, GitHub release
  creation, package build, and Twine upload with a dry-run default.

## [v7.0.5] - 2026-06-30

### Added

- Generated OpenAPI documents are now validated in tests for both OpenAPI 3.0.x
  and 3.1.x, including problem-details and legacy-error modes.
- A `scripts/release_check.py` guard verifies release metadata, optional clean
  tree and tag state, tests, lint, types, docs, package builds, wheel contents,
  and `twine check`.
- Runtime contracts documentation now describes framework error responses, auth
  inheritance, dependency lifecycle, response-model failures, and OpenAPI
  defaults.

### Changed

- Wheels now only package `responder` and its declared package data, keeping
  top-level docs, examples, and tests out of the installed wheel.
- CI now runs the release guard's metadata, build, wheel-content, and
  `twine check` path on every push and pull request.

### Fixed

- The Sphinx docs build now treats Markdown files as sources, making existing
  Markdown project pages and the changelog symlink resolvable from the toctree.

## [v7.0.4] - 2026-06-30

### Fixed

- HEAD responses now send headers without an ASGI response body while preserving
  the `Content-Length` a GET would have produced for non-streaming responses.
- Query-string parameters with blank values (for example `?q=`) are preserved
  instead of being treated as missing.
- Mutating `req.path_params` during a request can no longer poison the route
  resolution cache for later requests to the same concrete path.

## [v7.0.3] - 2026-06-30

### Fixed

- OpenAPI now documents legacy JSON error responses when
  `API(problem_details=False)` is set, and omits the `ProblemDetails` schema in
  that mode.
- `auth.optional()` now rejects malformed `Authorization` headers instead of
  treating them as anonymous requests.
- `problem_handler` now receives the underlying exception for validation,
  response-model, and after-hook failures, and a failing handler falls back to
  the original framework payload.
- Scoped auth insufficient-scope challenges now keep valid
  `WWW-Authenticate` formatting when the base challenge already has parameters.
- Dependency-resolution errors no longer duplicate the active dependency in the
  reported chain.

## [v7.0.2] - 2026-06-30

### Added

- `API(problem_handler=...)` can enrich framework-generated
  `application/problem+json` payloads with fields such as `type`, `instance`,
  application error codes, or links.
- Problem-details payloads include `request_id` when request ID middleware or
  structured logging is enabled.
- OpenAPI generation now registers a reusable `ProblemDetails` schema and
  documents common framework error responses (`400`, `401`, `403`, `404`,
  `405`, `413`, `422`, `500`, and `504` when configured).
- OpenAPI operations now receive default `operationId`, `summary`, and tags
  when route metadata does not supply them.
- Generated Python, JavaScript, TypeScript, Ruby, and PHP clients expose parsed
  problem details on `APIError.problem`; Python also surfaces `title`, `detail`,
  and `errors` convenience attributes.
- `auth.optional()` accepts missing credentials while still rejecting invalid
  credentials, injecting `None` for anonymous requests and documenting optional
  security in OpenAPI.
- `API(trace_dispatch=True)` emits debug logs for the documented dispatch
  stages: before hooks, auth, dependencies, handlers, and after hooks.
- `responder.testing.assert_problem(...)` provides a small assertion helper for
  problem-details responses.

### Changed

- Scoped auth failures now include a `WWW-Authenticate` insufficient-scope
  challenge when the wrapped scheme provides a challenge.
- Dependency-resolution errors include the dependency chain being resolved.

## [v7.0.1] - 2026-06-29

### Fixed

- Problem-details responses for `413` errors now use the RFC 9110 title
  `Content Too Large` consistently across Python runtimes.
- CI now installs the checked-out package editable during tests so cached wheels
  cannot mask source changes during release validation.

## [v7.0.0] - 2026-06-29

A major release focused on explicit runtime contracts: problem details by
default, first-class app auth (with scope/role checks), route-level dependency
guards, and clearer optional production-server packaging. Python 3.11 is now the
minimum, and the deprecated `request_model=` route option has been removed.

### Added

- `API(auth=...)` applies auth helpers to routes by default. Routes can opt out
  with `auth=None`.
- `ScopedAuth` and `auth.requires(...)` add lightweight scope/role checks on top
  of existing auth helpers, returning `403` when the principal lacks a required
  scope and documenting scoped OpenAPI security requirements.
- `dependencies=[Depends(...)]` on route decorators runs dependency-graph
  dependencies before the handler without injecting an unused return value.
- `responder[server]` keeps Granian as an optional production ASGI dependency.
  `uvicorn` remains in the default install and is still used by
  `api.run()` unless `server="granian"` is requested.
- `api.run(server="granian")` runs the current app with Granian's embedded ASGI
  server when `responder[server]` is installed, and raises a targeted install
  error when it is missing.
- Route dispatch ordering for before hooks, auth, dependency-guards, handler,
  and after hooks is now part of the documented v7 behavior.
- A v7 migration guide covering default error responses, auth inheritance,
  route dependency guards, removed request-model compatibility, and the server
  extra.

### Changed

- Framework-generated errors now use `application/problem+json` by default.
  `errors` remains an extension member when present (for example on 422s),
  so migration is media-type oriented instead of response-shape oriented.
  Pass `problem_details=False` to keep the legacy JSON/plain-text negotiation.
- Problem-details responses are serialized as JSON bytes regardless of the
  request's `Accept` header, and unhandled 500s use the same problem-details
  contract by default.
- Explicit `Depends(...)` parameters and registered dependencies take
  precedence over auth principal-name injection when they use the same handler
  parameter name; the authenticated principal remains available on request
  state.
- `req.method` now returns an exact `str`; the no-op exported `HTTPMethod`
  subclass has been removed.
- Python 3.11 is now the minimum supported Python version; internal datetime
  handling uses the stdlib `datetime.UTC` alias accordingly.

### Removed

- The deprecated `request_model=` route option and `req.state.validated`
  compatibility path have been removed. Use required Pydantic-typed handler
  parameters for request-body validation.
- The deprecated private `_resolve_dependency` route helper has been removed;
  the request dependency graph is resolved through the shared resolver.

### Fixed

- Byte-range requests now cap the number of requested ranges and coalesce
  overlapping or adjacent ranges before building single or multipart responses.
- Request-scoped dependency caching now distinguishes bound methods by
  instance, avoiding cross-instance value reuse.
- WebSocket routes now resolve inline `Depends(...)` parameters and run
  route-local `after=` hooks.
- Exceptions raised by after hooks now produce a controlled 500 response using
  the normal error contract.
- OpenAPI schemas now default missing API metadata to valid string values
  instead of emitting `null` for required `info` fields.

## [v6.6.1] - 2026-06-29

A small follow-up release focused on problem-details consistency, packaging
metadata, documentation, and internal route-dispatch maintainability.

### Changed

- `problem_details=True` now also applies to production response-model
  validation failures, returning `application/problem+json` for those
  framework-generated `500` responses.
- Refactored HTTP route dispatch internals into smaller helpers without changing
  public behavior.
- Updated project license metadata to the modern SPDX form used by current
  packaging tools.
- Refreshed the README with concise examples for route-local hooks, local
  dependencies, route-level auth, file-upload saving, and problem-details
  responses.

## [v6.6.0] - 2026-06-29

A backward-compatible release that adds route-local controls, explicit local
dependencies, problem-details errors, upload saving, and multipart byte ranges.

### Added

- **Route-local hooks** with `before=` and `after=` on `api.route()` and verb
  decorators. Global hooks still run, while route-local hooks apply only to the
  decorated endpoint.
- **Route-level auth enforcement** with `auth=`. Auth helpers such as
  `BearerAuth` now enforce access, register their OpenAPI security scheme when
  OpenAPI is enabled, and inject the authenticated principal into `user`,
  `principal`, or `auth` handler parameters.
- **`Depends(...)`** for explicit per-route dependency providers without app-wide
  registration. Providers can be sync, async, generator, or async-generator
  callables and can receive the current request or registered dependencies.
- **`problem_details=True`** on `API(...)` for RFC 9457-style framework errors
  using `application/problem+json`.
- **`await upload.save(path)`** on injected/uploaded `UploadFile` objects, with
  streaming writes and optional parent-directory creation.
- **Multipart byte-range responses** for `resp.file()` and `resp.stream_file()`
  when clients request multiple ranges.

### Changed

- Response format encoders now respect an explicit `Content-Type` already set on
  the response instead of overwriting it during serialization.

## [v6.5.3] - 2026-06-29

### Fixed

- Generated **TypeScript** clients now type-check under `tsc` / `deno check`.
  The opt-in `validate` helpers added in 6.5.2 emitted untyped parameters
  (implicit `any`) in the shared JS/TS validation code; the TypeScript output is
  now annotated. JavaScript output is unchanged.

## [v6.5.2] - 2026-06-29

A backward-compatible follow-up adding opt-in generated-client validation.

### Added

- Generated Python, JavaScript, and TypeScript clients now support opt-in
  runtime schema validation with `validate=True` / `validate: true`, raising
  `APIValidationError` when outgoing JSON bodies or successful JSON responses do
  not match the generated OpenAPI schemas.

## [v6.5.1] - 2026-06-29

A backward-compatible follow-up that rounds out the client generator.

### Added

- **`responder client <target>`** CLI subcommand generates a client from the
  same import target as `responder run` — `--lang`, `--class-name`, and
  `--output`/`-o` select the language, class name, and destination (stdout by
  default).
- Generated **Python clients emit `TypedDict` definitions** and **TypeScript
  clients exported interfaces** for OpenAPI component schemas, used for the
  request-body and success-response method signatures.

## [v6.5.0] - 2026-06-29

A backward-compatible release adding first-class client generation.

### Added

- **`responder.ext.clientgen`** generates small Python, JavaScript,
  TypeScript, Ruby, and PHP clients from Responder's OpenAPI schema.
  `generate_client(...)` returns source code, and `write_client(...)` writes a
  module to disk.
- **`API.generate_client(...)`** is the app-level convenience wrapper:
  `api.generate_client(class_name="ServiceClient")` returns source, while
  `api.generate_client("clients/service.py", class_name="ServiceClient")` writes
  a ready-to-import client module. Pass `language="typescript"` (or
  `javascript`, `ruby`, `php`, `python`) to select the target.
- Generated clients include method signatures from path/query parameters, JSON
  request bodies, bearer/basic/API-key header helpers, structured `APIError`,
  real HTTP transport, typed Python/TypeScript signatures where schema permits,
  and a Python `session=` hook so the same generated client can call
  `api.requests` in tests.

## [v6.4.0] - 2026-06-29

A backward-compatible release that makes typed path parameters and resumable
downloads much sharper in practice.

### Added

- **Typed path parameters now work even on plain route segments.** A route like
  ``/users/{id}`` can now declare ``id: int`` (or ``UUID``, etc.) and Responder
  validates/coerces the path value into that type, returning a ``422`` on bad
  input instead of passing a raw string through. WebSocket routes get the same
  coercion and close invalid typed path parameters with ``1008``.
- **Path-parameter OpenAPI got smarter.** The generated schema now reflects:
  - a bare handler annotation on a plain ``{id}`` segment,
  - ``Path(...)`` aliases/constraints/description metadata, and
  - the built-in ``{id:uuid}`` convertor as ``{type: string, format: uuid}``.
  Class-based views now generate method-specific parameters from ``on_get`` /
  ``on_post`` / etc., so one method's query parameters no longer disappear from
  the schema.
- **`If-Range` support for file responses.** ``resp.file()``,
  ``resp.stream_file()``, and ``resp.download()`` now honor ``If-Range`` so a
  stale resumable-download request automatically falls back to the full body
  instead of returning a partial response for the wrong representation.

## [v6.3.1] - 2026-06-29

### Fixed

- OpenAPI now emits query/header/cookie/path parameters at the **operation**
  level rather than the path-item level. The path-item placement (present since
  the typed-OpenAPI feature in 5.0) leaked a route's parameters onto sibling
  methods sharing the same path — e.g. a `@api.post` inherited a `@api.get`'s
  query parameters. Each operation now carries only its own parameters (also the
  placement most tooling and codegen expect).

## [v6.3.0] - 2026-06-29

A backward-compatible release adding sorting & filtering helpers that complete
the list-endpoint story alongside pagination.

### Added

- **`responder.ext.query`** — in-memory helpers for list endpoints (dicts or
  objects, no ORM coupling):
  - **`sort_items(items, spec, *, allowed=None)`** sorts by a ``name,-created``
    spec (``-`` = descending, multiple keys, ``None`` sorts last). Pass
    ``allowed=`` for a client-supplied sort so users can't order by arbitrary
    attributes — an out-of-list (or incomparable) field returns ``400``.
    ``parse_sort`` is exposed for sorting in the database yourself.
  - **`filter_items(items, filters)`** applies ``field == value`` equality and
    skips ``None`` values, so optional ``Query`` markers pass straight through.

  Together with ``Query`` markers and ``responder.ext.pagination`` they make a
  complete filter → sort → paginate pipeline.

## [v6.2.0] - 2026-06-29

A backward-compatible release adding pagination helpers for list endpoints.

### Added

- **`responder.ext.pagination`** — a generic `Page[T]` response envelope
  (`items`, `total`, `page`, `size`, `pages`) and a `paginate(items, *, page,
  size, total=None)` helper. `paginate` slices an in-memory collection by
  default, or wraps an already-sliced page when you pass `total=` (e.g. a
  `LIMIT/OFFSET` query). Pairs with the typed `Query` markers and
  `response_model=Page[Item]` — the OpenAPI schema documents the envelope inline,
  referencing your element model.
- Parametrized generic models (e.g. `Page[Item]`) are now emitted **inline** in
  OpenAPI (referencing their element components) rather than registered under a
  bracketed — and spec-invalid — component name.

## [v6.1.0] - 2026-06-29

A backward-compatible release that makes Server-Sent Events production-grade.

### Added

- **Production-grade `resp.sse`.** Server-Sent Events gain:
  - **JSON-encoded data** — a ``data`` value that is a ``dict``/``list`` is
    serialized automatically (great for structured/LLM streaming).
  - **Comment frames** via ``{"comment": "..."}`` (and raw ``bytes`` pass
    through verbatim).
  - **Opt-in heartbeat** — ``@resp.sse(heartbeat=15)`` emits a keepalive comment
    during idle periods (without interrupting the producer mid-event), so
    long-lived streams survive proxy idle-timeouts.
  - **``X-Accel-Buffering: no``** so events flush immediately behind nginx and
    similar proxies.
  - The ``@resp.sse(heartbeat=...)`` decorator-with-arguments form.
- **`req.last_event_id`** exposes the SSE ``Last-Event-ID`` request header, so a
  handler can resume a stream where a reconnecting client left off.

## [v6.0.2] - 2026-06-29

A bugfix release closing a set of cross-feature interaction defects found by an
adversarial review of the 5.1->6.0 work — including two remotely-triggerable
500s and a bypass of `max_request_size` on multipart uploads. Upgrading from
6.0.x is strongly recommended.

### Fixed

- **Remote 500 on file endpoints.** An `If-Modified-Since` header with a `-0000`
  timezone crashed every conditional file response (a naive-vs-aware datetime
  comparison sat outside the try/except). Both sides are now normalized to UTC.
- **`max_request_size` bypass + "Stream consumed" 500 on multipart.** Parsing a
  form/file body (`Form()`/`File()` markers or `req.media("files")`) now buffers
  through the size-checked body, so the cap is enforced (`413`) and the body
  stays readable afterward (`req.content`, `media("form")`, …) in any order.
  (This trades 5.5's spool-to-disk streaming for correctness; for true streaming
  uploads, read `req.stream()` directly.)
- **Recursive Pydantic models** registered an empty self-referential OpenAPI
  component; the real schema body is now emitted.
- **`APIKeyAuth(location="query")`** crashed on WebSocket routes (it read
  `.params`, which a Starlette WebSocket lacks); it now falls back to
  `.query_params`.
- **Callable-instance generator dependencies** (a class whose `__call__` yields)
  now run their teardown instead of leaking the generator object as the value.
- **`dependency_overrides`** now reaches into the app-scoped graph: overriding a
  dependency that an `app`-scoped dependency depends on resolves correctly (was a
  `DependencyScopeError` / stale cached value), and overrides restore per-key so
  nested blocks don't clobber each other.
- **Conditional responses.** A `304` now carries the negotiated `Vary` header,
  and a `Range` request with a matching validator returns `304` rather than a
  `206` body.
- OpenAPI now logs a warning on a component **name collision** (two distinct
  models sharing a `__name__`) instead of silently serving one for both.

## [v6.0.1] - 2026-06-29

### Fixed

- **`Annotated[...]` markers with a `None` default** (e.g.
  `token: Annotated[str, Header(None)] = None`) were not detected on Python
  3.10, where `get_type_hints` implicitly wraps the annotation in `Optional` —
  the marker is now found through the `Union`. (Latent since 5.3.)

## [v6.0.0] - 2026-06-28

A small, deliberate major release: **no new features** — it removes the
deprecation shims introduced during 5.x and flips a few defaults to the
more-correct behavior. Every change was announced with a `DeprecationWarning`
or an opt-in knob during 5.x, so code that runs clean under
`-W error::DeprecationWarning` on 5.6 upgrades without surprises. See the
[v6 migration guide](https://github.com/kennethreitz/responder/blob/main/docs/migration-v6.md).

### Removed

- **The case-insensitive `req.method` comparison shim.** `req.method` is now a
  plain uppercase `str`; compare against uppercase literals (`req.method ==
  "GET"`). (Announced in 5.0.)
- **The single-unnamed-parameter dependency-provider shim.** A provider's
  request parameter must be named `req`/`request` or annotated `Request`;
  otherwise resolution raises `DependencyResolutionError`. (Announced in 5.0.)

### Changed

- **`await req.media("files")`** returns streaming `UploadFile` objects keyed by
  field name instead of a fully-buffered bytes-dict; `File()` markers are the
  typed equivalent. (Deprecated in 5.6.)
- **JSON defaults to `ensure_ascii=False`** (raw UTF-8). This changes response
  bytes and `auto_etag` values for non-ASCII payloads; pass
  `API(json_ensure_ascii=True)` to restore `\uXXXX` escaping. (Knob added in 5.6.)
- **`Vary: Accept`** is now sent by default on content-negotiated responses
  (correct for shared caches); pass `API(auto_vary=False)` to opt out. (Opt-in
  added in 5.1.)
- **`Route.__hash__`** no longer includes `before_request`, so routes that
  compare equal now hash equal.

## [v5.6.0] - 2026-06-28

A backward-compatible release that stages the breaking changes coming in
Responder 6.0 — adding the opt-in knobs and deprecation warnings so you can
adapt on 5.x first. See the [v6 migration guide](https://github.com/kennethreitz/responder/blob/main/docs/migration-v6.md).

### Added

- **`API(json_ensure_ascii=...)`** controls JSON non-ASCII escaping. The default
  stays `True` (escape as `\uXXXX`) in 5.x and flips to `False` (raw UTF-8) in
  6.0; set it explicitly to lock in either behavior.

### Deprecated

- **`await req.media("files")`** (the fully-buffered bytes-dict) now emits a
  `DeprecationWarning`. In 6.0 it returns streaming `UploadFile` objects; use
  `File()` markers for the new API today. The bytes-dict is still returned in
  5.x.
- Documented the project's **deprecation policy** and the full 6.0 breaking-change
  list in `docs/migration-v6.md`.

## [v5.5.0] - 2026-06-28

A backward-compatible release adding type-driven file uploads and form fields,
completing the typed-parameter surface. No existing call signatures change.

### Added

- **`File()` and `Form()` markers.** `File()` injects an uploaded file as an
  `UploadFile` (read with `await f.read()`, or stream it in chunks — large
  uploads are spooled to disk by Starlette's parser rather than held in
  memory); `Form()` injects a form field (urlencoded or multipart), coerced and
  validated like `Query()`. A sequence annotation (`list[UploadFile]`,
  `list[str]`) collects repeated fields. Both support the `Annotated[...]` form.
- **`responder.UploadFile`** is exported for annotating upload parameters.
- **Multipart OpenAPI.** Routes with `File()`/`Form()` markers generate a
  `multipart/form-data` (or `application/x-www-form-urlencoded`) request body —
  files as `{type: string, format: binary}` — so the interactive docs show a
  file picker.

The existing `await req.media("files")` bytes-dict contract is unchanged.

## [v5.4.0] - 2026-06-28

A backward-compatible release focused on testing, operations, and HTTP
correctness. No existing call signatures change.

### Added

- **`api.dependency_overrides(**overrides)`** — a context manager that swaps
  dependencies for tests and restores them on exit. Values may be bare objects
  (wrapped automatically) or provider callables (which still receive
  sub-dependencies and the request). Overrides are request-scoped, so they
  replace and bypass the cache of an `app`-scoped dependency too.
- **Health checks.** `api.add_health_check(name, check)` registers a readiness
  check (sync or async; passes unless it returns `False` or raises) and
  `API(health_route="/health")` serves the aggregate — `200` with per-check
  JSON when all pass, `503` otherwise. The route is excluded from the OpenAPI
  schema.
- **Named routes.** `@api.route(..., name="...")` (and the verb decorators /
  `add_route`) name a route so `api.url_for("name", **params)` can reverse it by
  string — decoupling URL generation from the endpoint's function identity
  (so lambdas and shared names are addressable). Works for WebSocket routes too.

### Fixed

- **Conditional requests for served files.** `resp.file()`, `resp.stream_file()`,
  and `resp.download()` now set a stat-based weak `ETag` and `Last-Modified` by
  default, so `If-None-Match` / `If-Modified-Since` get a `304` (and `file()`
  no longer reads the whole file to hash it under `auto_etag`). Range requests
  are unaffected. Pass `conditional=False` to opt out.

## [v5.3.0] - 2026-06-28

A backward-compatible release that finishes the type-driven I/O and OpenAPI
authoring story: `Annotated[]` markers, generic response models, and
first-class route/operation metadata. No existing call signatures change.

### Added

- **`Annotated[]` marker form.** `Query`/`Header`/`Cookie`/`Path` markers can
  now be written as `q: Annotated[int, Query(ge=1)]` (PEP 593), keeping the
  parameter's default value in the usual slot — the FastAPI-familiar style — in
  addition to the existing `q: int = Query(...)` form. Constraints, aliases,
  and OpenAPI emission work identically either way.
- **Generic `response_model`.** `response_model=list[Model]`, tuples, and unions
  (`Model | ErrorModel`) are now validated and serialized via a `TypeAdapter`
  and documented correctly in OpenAPI — an `array` with an `items` `$ref` for a
  list, `oneOf`/`anyOf` for a union, with the referenced models hoisted into
  `components/schemas`. (`response_model=list[Model]` previously registered a
  bogus schema named `list`.) A bare `-> list[Model]` *return annotation* still
  appears in the schema but stays un-validated at runtime, so existing handlers
  returning loose data keep working.
- **Route/operation metadata.** `tags`, `summary`, `description`,
  `operation_id`, and `deprecated` kwargs on `@api.route` (and the verb
  decorators) flow into the generated OpenAPI operation; a docstring-YAML block
  still overrides them.
- **`API(openapi_servers=[...])`** populates the OpenAPI `servers` list.

## [v5.2.0] - 2026-06-28

A backward-compatible release that rounds out the security story: a batteries-
included authentication extension that wires straight into OpenAPI (so the
interactive docs get an **Authorize** button), and an opt-in security-headers
middleware. No existing call signatures change.

### Added

- **`responder.ext.auth`** — `BearerAuth`, `BasicAuth`, and `APIKeyAuth`
  schemes. Each is a callable that extracts the credential, runs your
  (sync or async) `verify` callback, and returns the principal — or raises
  `401` with the correct `WWW-Authenticate` challenge. Use one as a dependency
  to inject the principal into a handler, or call it directly. For static
  secrets, pass them inline (`BearerAuth(tokens=[...])`,
  `APIKeyAuth(keys=[...])`, `BasicAuth(credentials={...})`) and the scheme
  compares them in constant time.
- **OpenAPI security schemes.** `api.add_security_scheme(name, scheme)` (or
  `scheme.register(api)`) populates `components.securitySchemes` so Swagger /
  ReDoc render an **Authorize** button. A `security=` kwarg on routes (and the
  verb decorators) marks which operations require auth; `default=True` requires
  a scheme on every operation.
- **`API(security_headers=True)`** and **`responder.middleware.SecurityHeadersMiddleware`**
  — add `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and
  `Referrer-Policy: strict-origin-when-cross-origin` to every response (opt-in).
  Pass a dict to add a `content_security_policy` / `permissions_policy` or
  override any header. Headers a handler set itself are preserved.

### Changed

- Dependency providers may now be **callable instances** with an async
  `__call__` (previously only plain async functions were awaited; an async
  `__call__` was mistakenly run in a thread). This is what lets an auth scheme
  object be used directly as a dependency.

## [v5.1.0] - 2026-06-28

A backward-compatible follow-up that finishes what v5 started: it makes the
type-driven OpenAPI and typed-parameter features correct for the common cases
they missed, turns the documented-but-absent HSTS header into a real one, and
adds the small ergonomics the typed surface implies. No existing call
signatures change.

### Fixed

- **Type-driven OpenAPI now emits valid documents for nested models.** A model
  containing another model previously produced a dangling `#/$defs/...`
  reference (the nested schema was never registered as a component), so Swagger
  UI / ReDoc / codegen could not resolve it. Each nested model is now hoisted
  into its own top-level `components/schemas` entry with rewritten `$ref`s.
- **OpenAPI output matches the declared dialect.** Under a `3.0.x` version,
  `Optional[...]` fields (which Pydantic v2 emits as `anyOf: [..., {type: null}]`,
  invalid in 3.0) are down-converted to `nullable`, and array-valued `examples`
  are singularized — so the document validates. `3.1` output is unchanged.
- **`Query()`/`Header()`/`Cookie()`/`Path()` markers now enforce their
  constraints.** `Query(min_length=3)`, `Query(gt=0)`, etc. were silently
  ignored (validation was built from the bare annotation); they now apply and
  return `422` on violation, and a typo'd keyword (e.g. `Query(dafault=5)`) now
  raises `TypeError` at definition time instead of silently making the parameter
  required. A marker's `description`/`deprecated` now appear in the OpenAPI spec.
- **`enable_hsts=True` now sends a real `Strict-Transport-Security` header**
  (in addition to the existing HTTP→HTTPS redirect). It previously only
  redirected, despite the docs promising browsers "see the HSTS header."

### Added

- **HTTP verb shortcut decorators** — `@api.get`, `@api.post`, `@api.put`,
  `@api.patch`, `@api.delete`, and `@api.websocket_route`, plus the same on
  route groups. Registering `@api.get("/x")` and `@api.post("/x")` as separate
  handlers on one path now works (same-path routes are allowed when their
  methods are disjoint).
- **`resp.delete_cookie(key, ...)`** — expire a cookie on the client (mirrors
  `set_cookie`'s `path`/`domain`/`secure`/`httponly`/`samesite`).
- **`resp.vary(*fields)`** — add field names to the `Vary` header (merged and
  de-duplicated). New `API(auto_vary=True)` emits `Vary: Accept` on
  content-negotiated responses (correct for shared caches); off by default,
  slated to default on in 6.0.
- **`responder.middleware.HSTSMiddleware`** — the HSTS header middleware, usable
  directly via `add_middleware` to customize `max_age`/`preload`.

## [v5.0.0] - 2026-06-28

A major release: fully type-driven request/response I/O, composable dependency
injection, plan-driven OpenAPI, secure-by-default sessions, and a deferred
middleware stack — layered onto the unchanged `(req, resp)` core. Breaking
changes are staged behind the [v5 migration guide](https://github.com/kennethreitz/responder/blob/main/docs/migration-v5.md).

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

[Unreleased]: https://github.com/kennethreitz/responder/compare/v8.3.0..HEAD
[v8.3.0]: https://github.com/kennethreitz/responder/compare/v8.2.3..v8.3.0
[v8.2.3]: https://github.com/kennethreitz/responder/compare/v8.2.2..v8.2.3
[v8.2.2]: https://github.com/kennethreitz/responder/compare/v8.2.1..v8.2.2
[v8.2.1]: https://github.com/kennethreitz/responder/compare/v8.2.0..v8.2.1
[v8.2.0]: https://github.com/kennethreitz/responder/compare/v8.1.0..v8.2.0
[v8.1.0]: https://github.com/kennethreitz/responder/compare/v8.0.2..v8.1.0
[v8.0.2]: https://github.com/kennethreitz/responder/compare/v8.0.1..v8.0.2
[v8.0.1]: https://github.com/kennethreitz/responder/compare/v8.0.0..v8.0.1
[v8.0.0]: https://github.com/kennethreitz/responder/compare/v7.3.0..v8.0.0
[v7.3.0]: https://github.com/kennethreitz/responder/compare/v7.2.1..v7.3.0
[v7.2.1]: https://github.com/kennethreitz/responder/compare/v7.2.0..v7.2.1
[v7.2.0]: https://github.com/kennethreitz/responder/compare/v7.1.3..v7.2.0
[v7.1.3]: https://github.com/kennethreitz/responder/compare/v7.1.2..v7.1.3
[v7.1.2]: https://github.com/kennethreitz/responder/compare/v7.1.1..v7.1.2
[v7.1.1]: https://github.com/kennethreitz/responder/compare/v7.1.0..v7.1.1
[v7.1.0]: https://github.com/kennethreitz/responder/compare/v7.0.5..v7.1.0
[v7.0.5]: https://github.com/kennethreitz/responder/compare/v7.0.4..v7.0.5
[v7.0.4]: https://github.com/kennethreitz/responder/compare/v7.0.3..v7.0.4
[v7.0.3]: https://github.com/kennethreitz/responder/compare/v7.0.2..v7.0.3
[v7.0.2]: https://github.com/kennethreitz/responder/compare/v7.0.1..v7.0.2
[v7.0.1]: https://github.com/kennethreitz/responder/compare/v7.0.0..v7.0.1
[v7.0.0]: https://github.com/kennethreitz/responder/compare/v6.6.1..v7.0.0
[v6.6.1]: https://github.com/kennethreitz/responder/compare/v6.6.0..v6.6.1
[v6.6.0]: https://github.com/kennethreitz/responder/compare/v6.5.3..v6.6.0
[v6.5.3]: https://github.com/kennethreitz/responder/compare/v6.5.2..v6.5.3
[v6.5.2]: https://github.com/kennethreitz/responder/compare/v6.5.1..v6.5.2
[v6.5.1]: https://github.com/kennethreitz/responder/compare/v6.5.0..v6.5.1
[v6.5.0]: https://github.com/kennethreitz/responder/compare/v6.4.0..v6.5.0
[v6.4.0]: https://github.com/kennethreitz/responder/compare/v6.3.1..v6.4.0
[v6.3.1]: https://github.com/kennethreitz/responder/compare/v6.3.0..v6.3.1
[v6.3.0]: https://github.com/kennethreitz/responder/compare/v6.2.0..v6.3.0
[v6.2.0]: https://github.com/kennethreitz/responder/compare/v6.1.0..v6.2.0
[v6.1.0]: https://github.com/kennethreitz/responder/compare/v6.0.2..v6.1.0
[v6.0.2]: https://github.com/kennethreitz/responder/compare/v6.0.1..v6.0.2
[v6.0.1]: https://github.com/kennethreitz/responder/compare/v6.0.0..v6.0.1
[v6.0.0]: https://github.com/kennethreitz/responder/compare/v5.6.0..v6.0.0
[v5.6.0]: https://github.com/kennethreitz/responder/compare/v5.5.0..v5.6.0
[v5.5.0]: https://github.com/kennethreitz/responder/compare/v5.4.0..v5.5.0
[v5.4.0]: https://github.com/kennethreitz/responder/compare/v5.3.0..v5.4.0
[v5.3.0]: https://github.com/kennethreitz/responder/compare/v5.2.0..v5.3.0
[v5.2.0]: https://github.com/kennethreitz/responder/compare/v5.1.0..v5.2.0
[v5.1.0]: https://github.com/kennethreitz/responder/compare/v5.0.0..v5.1.0
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
