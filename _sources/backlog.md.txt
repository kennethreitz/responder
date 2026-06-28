# Backlog

A loose list of things we'd like to add. If one of these would make Responder
better for you, an issue or pull request is very welcome — several make good
first contributions. See {doc}`sandbox` to get a development environment going.

## Future Ideas

- **First-class API client generation from the OpenAPI schema** — turn the spec
  Responder already generates into a typed Python client.
- **Multipart (multiple-range) `206` responses** — Responder already serves a
  single byte range (`resp.file` and `resp.stream_file` honor `Range: bytes=...`
  with a `206` and a `Content-Range` header); answering several ranges at once in
  one `multipart/byteranges` body is still open.
- **`If-Range` support for safe resumable downloads** — revalidate a client's
  cached range against the current `ETag` / `Last-Modified` so a changed file
  restarts the transfer instead of stitching together stale bytes.
- **Richer path-parameter schemas in OpenAPI** — path parameters are documented
  from their URL convertor today (`{id:int}` becomes an integer), so a segment
  typed only by a handler annotation or `Path()` marker — `/users/{id}` with
  `id: int` — or a `{id:uuid}` segment still shows up as a plain string.

## Recently shipped

v5 delivered most of the original "document path parameters from their types"
idea. The OpenAPI schema is now generated from each route's methods,
body/response models, and `Query` / `Header` / `Cookie` markers, so routes appear
even without a YAML docstring — and any docstring YAML is deep-merged on top as an
override. Path parameters are documented from their URL convertor. See
{doc}`tour` for the full type-driven OpenAPI story.
