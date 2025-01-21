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

    pip install --upgrade 'responder'

Include support for all extensions and interfaces:

    pip install --upgrade 'responder[full]'

Individual optional installation extras are:

- graphql: Adds GraphQL support via Graphene
- openapi: Adds OpenAPI/Swagger interface support

Install package with CLI and GraphQL support:

    uv pip install --upgrade 'responder[cli,graphql]'

Alternatively, install directly from the repository:

    pip install 'responder[full] @ git+https://github.com/kennethreitz/responder.git'

Responder supports **Python 3.7+**.

# The Basic Idea

The primary concept here is to bring the niceties that are brought forth from both Flask
and Falcon and unify them into a single framework, along with some new ideas I have. I
also wanted to take some of the API primitives that are instilled in the Requests
library and put them into a web framework. So, you'll find a lot of parallels here with
Requests.

- Setting `resp.content` sends back bytes.
- Setting `resp.text` sends back unicode, while setting `resp.html` sends back HTML.
- Setting `resp.media` sends back JSON/YAML (`.text`/`.html`/`.content` override this).
- Case-insensitive `req.headers` dict (from Requests directly).
- `resp.status_code`, `req.method`, `req.url`, and other familiar friends.

## Ideas

- Flask-style route expression, with new capabilities -- all while using Python 3.6+'s
  new f-string syntax.
- I love Falcon's "every request and response is passed into to each view and mutated"
  methodology, especially `response.media`, and have used it here. In addition to
  supporting JSON, I have decided to support YAML as well, as Kubernetes is slowly
  taking over the world, and it uses YAML for all the things. Content-negotiation and
  all that.
- **A built in testing client that uses the actual Requests you know and love**.
- The ability to mount other WSGI apps easily.
- Automatic gzipped-responses.
- In addition to Falcon's `on_get`, `on_post`, etc methods, Responder features an
  `on_request` method, which gets called on every type of request, much like Requests.
- A production static file server is built-in.
- Uvicorn built-in as a production web server. I would have chosen Gunicorn, but it
  doesn't run on Windows. Plus, Uvicorn serves well to protect against slowloris
  attacks, making nginx unnecessary in production.
- GraphQL support, via Graphene. The goal here is to have any GraphQL query exposable at
  any route, magically.
- Provide an official way to run webpack.

## Development

See [Development Sandbox](https://responder.kennethreitz.org/sandbox.html).

## Supported by

[![JetBrains logo.](https://resources.jetbrains.com/storage/products/company/brand/logos/jetbrains.svg)](https://jb.gg/OpenSourceSupport)

Special thanks to the kind people at JetBrains s.r.o. for supporting us with
excellent development tooling.
