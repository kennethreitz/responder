# Migrating to Responder 6.0

Responder 6.0 is a small, deliberate major release. It adds **no new features**:
it removes the deprecation shims introduced during 5.x and flips a handful of
defaults to the more-correct behavior. The `(req, resp)` core, dependency
injection, typed I/O, auth, and uploads are all unchanged.

Every change below was announced with a `DeprecationWarning` (or an opt-in knob)
in a 5.x release, so you can fix your code on 5.x first and upgrade to 6.0 with
no surprises. Run your test suite with `-W error::DeprecationWarning` to surface
everything that needs attention.

## Deprecation policy

A behavior that changes in a major release is first deprecated in a minor: it
keeps working but emits a `DeprecationWarning` (or is gated behind an opt-in
flag) for at least one minor-release cycle before the major flips it.

## Breaking changes

| Change | Staged in | What to do |
|---|---|---|
| `req.method` returns a plain uppercase `str` (the case-insensitive `HTTPMethod` shim is removed) | 5.0 | Compare against uppercase literals: `req.method == "GET"`. |
| Dependency providers must name their request parameter `req`/`request` (or annotate it `Request`); the single-unnamed-parameter shim is removed | 5.0 | Rename the parameter, or annotate it `Request`. |
| `await req.media("files")` returns streaming `UploadFile` objects instead of a fully-buffered bytes-dict | 5.6 | Use `File()` markers (`async def h(req, resp, *, f: UploadFile = File(...))`), or read `.filename` / `await f.read()` off the returned `UploadFile`. |
| JSON serialization defaults to `ensure_ascii=False` (raw UTF-8) | 5.6 | No action for most apps. If you depend on `\uXXXX` escaping (or on exact `ETag` values over non-ASCII bodies), pass `API(json_ensure_ascii=True)`. |
| `Vary: Accept` is sent by default on content-negotiated responses | 5.1 | No action (this is shared-cache-correct). To opt out, don't set it / strip the header in middleware. |

## Minor invariant fix

`Route.__hash__` no longer includes `before_request`, so two routes that compare
equal now hash equal. This only affects code that put `Route` objects in a set or
dict keyed by identity-with-`before_request`, which is not a public pattern.

## Not changing

The `(req, resp)` handler signature, the `Query(...)`-in-default marker form,
docstring-YAML OpenAPI overrides, and the permissive default `allowed_hosts=["*"]`
all stay as they are.
