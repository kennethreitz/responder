# Responder: a familiar HTTP Service Framework for Python

[![ci-tests](https://github.com/kennethreitz/responder/actions/workflows/test.yaml/badge.svg)](https://github.com/kennethreitz/responder/actions/workflows/test.yaml)
[![ci-docs](https://github.com/kennethreitz/responder/actions/workflows/docs.yaml/badge.svg)](https://github.com/kennethreitz/responder/actions/workflows/docs.yaml)
[![Documentation Status](https://github.com/kennethreitz/responder/actions/workflows/pages/pages-build-deployment/badge.svg)](https://responder.kennethreitz.org/)
[![version](https://img.shields.io/pypi/v/responder.svg)](https://pypi.org/project/responder/)
[![license](https://img.shields.io/pypi/l/responder.svg)](https://pypi.org/project/responder/)
[![python-versions](https://img.shields.io/pypi/pyversions/responder.svg)](https://pypi.org/project/responder/)
[![downloads](https://static.pepy.tech/badge/responder/month)](https://pepy.tech/project/responder)
[![contributors](https://img.shields.io/github/contributors/kennethreitz/responder.svg)](https://github.com/kennethreitz/responder/graphs/contributors)
[![status](https://img.shields.io/pypi/status/responder.svg)](https://pypi.org/project/responder/)

[![responder-synopsis](https://farm2.staticflickr.com/1959/43750081370_a4e20752de_o_d.png)](https://responder.readthedocs.io)

Responder is powered by [Starlette](https://www.starlette.io/).
[View documentation](https://responder.readthedocs.io).

Responder gets you an ASGI app, with a production static files server pre-installed,
Jinja templating, and a production webserver based on uvloop, automatically serving
up requests with gzip compression.
The `async` declaration within the example program is optional.

## Testimonials

> "Pleasantly very taken with python-responder.
> [@kennethreitz](https://x.com/kennethreitz42) at his absolute best." —Rudraksh
> M.K.

> "ASGI is going to enable all sorts of new high-performance web services. It's awesome
> to see Responder starting to take advantage of that." — Tom Christie author of
> [Django REST Framework](https://www.django-rest-framework.org/)

> "I love that you are exploring new patterns. Go go go!" — Danny Greenfield, author of
> [Two Scoops of Django](https://www.feldroy.com/two-scoops-press#two-scoops-of-django)

## More Examples

See
[the documentation's feature tour](https://responder.readthedocs.io/tour.html)
for more details on features available in Responder.

# Installing Responder

Install the most recent stable release:

    pip install --upgrade responder

Alternatively, install directly from the repository:

    pip install 'responder @ git+https://github.com/kennethreitz/responder.git'

Responder supports **Python 3.9+**.

# The Basic Idea

The primary concept here is to bring the niceties from both Flask and Falcon and
unify them into a single framework. You'll find a familiar API with a clean,
Pythonic design.

- Setting `resp.text` sends back unicode, while setting `resp.html` sends back HTML.
- Setting `resp.media` sends back JSON/YAML (`.text`/`.html`/`.content` override this).
- Setting `resp.content` sends back bytes.
- Use `resp.file("path")` to serve files with automatic content-type detection.
- Case-insensitive `req.headers` dict.
- `resp.status_code`, `req.method`, `req.url`, and other familiar friends.

## Features

- Flask-style route expressions with f-string syntax and type convertors
  (`str`, `int`, `float`, `uuid`, `path`).
- HTTP method filtering: `@api.route("/data", methods=["GET"])`.
- Every request and response is passed into each view and mutated — including
  `response.media` for JSON/YAML content negotiation.
- Built-in test client powered by Starlette's TestClient.
- Mount other WSGI/ASGI apps at subroutes.
- Automatic gzip compression.
- Class-based views with `on_get`, `on_post`, `on_request` methods.
- GraphQL support via Graphene with `api.graphql()`.
- OpenAPI schema generation with interactive docs.
- Lifespan context managers for startup/shutdown.
- Custom exception handlers.
- Before-request hooks with short-circuit support.
- Cookie-based sessions.
- WebSocket support.
- Background tasks.
- Production uvicorn server built-in.

## Development

See [Development Sandbox](https://responder.kennethreitz.org/sandbox.html).

## Supported by

[![JetBrains logo.](https://resources.jetbrains.com/storage/products/company/brand/logos/jetbrains.svg)](https://jb.gg/OpenSourceSupport)

Special thanks to the kind people at JetBrains s.r.o. for supporting us with
excellent development tooling.
