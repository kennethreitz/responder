# Backlog

A loose list of things we'd like to add. If one of these would make Responder
better for you, an issue or pull request is very welcome — several make good
first contributions. See {doc}`sandbox` to get a development environment going.

## Future Ideas

Nothing queued here right now.

## Recently shipped

v6.6 added route-local hooks, route-level auth, `Depends(...)`, opt-in
`application/problem+json` framework errors, `UploadFile.save(...)`, and
multipart byte-range responses for `resp.file()` / `resp.stream_file()`.

v6.5 added first-class Python, JavaScript, TypeScript, Ruby, and PHP client
generation from Responder's OpenAPI schema, with real HTTP transport and a
Python in-process `session=` hook for tests.

v6.4 added `If-Range` support for safe resumable downloads, typed path-parameter
coercion on plain route segments, UUID path-parameter schemas, and richer
operation-level path/query parameter generation for class-based views.

v5 delivered most of the original "document path parameters from their types"
idea. The OpenAPI schema is now generated from each route's methods,
body/response models, and `Query` / `Header` / `Cookie` markers, so routes appear
even without a YAML docstring — and any docstring YAML is deep-merged on top as an
override. Path parameters are documented from their URL convertor. See
{doc}`tour` for the full type-driven OpenAPI story.
