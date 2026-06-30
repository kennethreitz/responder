# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/kennethreitz/responder/compare/v7.1.1..HEAD
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
