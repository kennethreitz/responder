# Responder: a familiar HTTP Service Framework for Python

[![Build Status](https://travis-ci.org/kennethreitz/responder.svg?branch=master)](https://travis-ci.org/kennethreitz/responder)
[![Documentation Status](https://readthedocs.org/projects/mybinder/badge/?version=latest)](https://responder.readthedocs.io/en/latest/)
[![image](https://img.shields.io/pypi/v/responder.svg)](https://pypi.org/project/responder/)
[![image](https://img.shields.io/pypi/l/responder.svg)](https://pypi.org/project/responder/)
[![image](https://img.shields.io/pypi/pyversions/responder.svg)](https://pypi.org/project/responder/)
[![image](https://img.shields.io/github/contributors/kennethreitz/responder.svg)](https://github.com/kennethreitz/responder/graphs/contributors)

[![](https://farm2.staticflickr.com/1959/43750081370_a4e20752de_o_d.png)](https://python-responder.org/)


Powered by [Starlette](https://www.starlette.io/). That `async` declaration is optional. [View documentation](https://python-responder.org).

This gets you a ASGI app, with a production static files server pre-installed, jinja2 templating (without additional imports), and a production webserver based on uvloop, serving up requests with gzip compression automatically.


## Testimonials

> "Pleasantly very taken with python-responder. [@kennethreitz](https://twitter.com/kennethreitz) at his absolute best." —Rudraksh M.K.

> "ASGI is going to enable all sorts of new high-performance web services. It's awesome to see Responder starting to take advantage of that." — Tom Christie author of [Django REST Framework](https://www.django-rest-framework.org/)

> "I love that you are exploring new patterns. Go go go!" — Danny Greenfield, author of [Two Scoops of Django]()


## More Examples

See [the documentation's feature tour](https://python-responder.org/en/latest/tour.html) for more details on features available in Responder.


# Installing Responder

Install the latest release:


    $ pipenv install responder --pre
    ✨🍰✨


Or, install from the development branch:

    $ pipenv install -e git+https://github.com/kennethreitz/responder.git#egg=responder

Only **Python 3.6+** is supported.


# The Basic Idea

The primary concept here is to bring the niceties that are brought forth from both Flask and Falcon and unify them into a single framework, along with some new ideas I have. I also wanted to take some of the API primitives that are instilled in the Requests library and put them into a web framework. So, you'll find a lot of parallels here with Requests.

- Setting `resp.content` sends back bytes.
- Setting `resp.text` sends back unicode, while setting `resp.html` sends back HTML.
- Setting `resp.media` sends back JSON/YAML (`.text`/`.html`/`.content` override this).
- Case-insensitive `req.headers` dict (from Requests directly).
- `resp.status_code`, `req.method`, `req.url`, and other familiar friends.


## Ideas

- Flask-style route expression, with new capabilities -- all while using Python 3.6+'s new f-string syntax.
- I love Falcon's "every request and response is passed into to each view and mutated" methodology, especially `response.media`, and have used it here. In addition to supporting JSON, I have decided to support YAML as well, as Kubernetes is slowly taking over the world, and it uses YAML for all the things. Content-negotiation and all that.
- **A built in testing client that uses the actual Requests you know and love**.
- The ability to mount other WSGI apps easily.
- Automatic gzipped-responses.
- In addition to Falcon's `on_get`, `on_post`, etc methods, Responder features an `on_request` method, which gets called on every type of request, much like Requests.
- A production static file server is built-in.
- Uvicorn built-in as a production web server. I would have chosen Gunicorn, but it doesn't run on Windows. Plus, Uvicorn serves well to protect against slowloris attacks, making nginx unnecessary in production.
- GraphQL support, via Graphene. The goal here is to have any GraphQL query exposable at any route, magically.
- Provide an official way to run webpack.


----------

[![hacktoberfest](https://hacktoberfest.digitalocean.com/assets/hacktoberfest-2018-social-card-c8d2e1489f647f2e0a26e6f598adeb760872818905b34cd437afc7ac2857ceab.png)](https://hacktoberfest.digitalocean.com/)
