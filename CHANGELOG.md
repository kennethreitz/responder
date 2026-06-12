# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

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
