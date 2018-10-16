# Responder: a familiar HTTP Service Framework for Python

[![Build Status](https://travis-ci.org/kennethreitz/responder.svg?branch=master)](https://travis-ci.org/kennethreitz/responder)
[![Documentation Status](https://readthedocs.org/projects/mybinder/badge/?version=latest)](https://responder.readthedocs.io/en/latest/)
[![image](https://img.shields.io/pypi/v/responder.svg)](https://pypi.org/project/responder/)
[![image](https://img.shields.io/pypi/l/responder.svg)](https://pypi.org/project/responder/)
[![image](https://img.shields.io/pypi/pyversions/responder.svg)](https://pypi.org/project/responder/)
[![image](https://img.shields.io/github/contributors/kennethreitz/responder.svg)](https://github.com/kennethreitz/responder/graphs/contributors)

[![](https://github.com/kennethreitz/responder/raw/master/ext/small.jpg)](http://python-responder.org/)

The Python world certainly doesn't need more web frameworks. But, it does need more creativity, so I thought I'd spread some [Hacktoberfest](https://hacktoberfest.digitalocean.com/) spirit around, bring some of my ideas to the table, and see what I could come up with.

```python
import responder

api = responder.API()

@api.route("/{greeting}")
async def greet_world(req, resp, *, greeting):
    resp.text = f"{greeting}, world!"

if __name__ == '__main__':
    api.run()
```

That `async` declaration is optional. [View documentation](http://python-responder.org).

This gets you a ASGI app, with a production static files server pre-installed, jinja2 templating (without additional imports), and a production webserver based on uvloop, serving up requests with gzip compression automatically.


## Testimonials

> "Pleasantly very taken with python-responder. [@kennethreitz](https://twitter.com/kennethreitz) at his absolute best." —Rudraksh M.K.

> "ASGI is going to enable all sorts of new high-performance web services. It's awesome to see Responder starting to take advantage of that." — Tom Christie author of [Django REST Framework](https://www.django-rest-framework.org/)

> "I love that you are exploring new patterns. Go go go!" — Danny Greenfield, author of [Two Scoops of Django]()

> "Love what I have seen while it's in progress! Many features of Responder are from my wishlist for Flask, and it's even faster and even easier than Flask!" — Luna C.

## More Examples

Class-based views (and setting some headers and stuff):

```python
@api.route("/{greeting}")
class GreetingResource:
    def on_request(req, resp, *, greeting):   # or on_get...
        resp.text = f"{greeting}, world!"
        resp.headers.update({'X-Life': '42'})
        resp.status_code = api.status_codes.HTTP_416
```

Render a template, with arguments:

```python
@api.route("/{greeting}")
def greet_world(req, resp, *, greeting):
    resp.content = api.template("index.html", greeting=greeting)
```

The `api` instance is available as an object during template rendering.

Here, you can spawn off a background thread to run any function, out-of-request:

```python
@api.route("/")
def hello(req, resp):

    @api.background.task
    def sleep(s=10):
        time.sleep(s)
        print("slept!")

    sleep()
    resp.content = "processing"
```

And even serve a GraphQL API:

```python
import graphene

class Query(graphene.ObjectType):
    hello = graphene.String(name=graphene.String(default_value="stranger"))

    def resolve_hello(self, info, name):
        return "Hello " + name

api.add_route("/graph", graphene.Schema(query=Query))
```

We can then send a query to our service:

```pycon
>>> requests = api.session()
>>> r = requests.get("http://;/graph", params={"query": "{ hello }"})
>>> r.json()
{'data': {'hello': 'Hello stranger'}}
```

Or, request YAML back:

```pycon
>>> r = requests.get("http://;/graph", params={"query": "{ hello(name:\"john\") }"}, headers={"Accept": "application/x-yaml"})
>>> print(r.text)
data: {hello: Hello john}

```

Want HSTS?

```
api = responder.API(enable_hsts=True)
```

Boom.


# Installing Responder

Install the latest release:


    $ pipenv install responder
    ✨🍰✨


Or, install from the development branch:

    $ pipenv install -e git+https://github.com/kennethreitz/responder.git#egg=responder

Only **Python 3.6+** is supported.


# The Basic Idea

The primary concept here is to bring the niceties that are brought forth from both Flask and Falcon and unify them into a single framework, along with some new ideas I have. I also wanted to take some of the API primitives that are instilled in the Requests library and put them into a web framework. So, you'll find a lot of parallels here with Requests.

- Setting `resp.text` sends back unicode, while setting `resp.content` sends back bytes.
- Setting `resp.media` sends back JSON/YAML (`.text`/`.content` override this).
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

## Future Ideas

- Cookie-based sessions are currently an afterthought, as this is an API framework, but websites are APIs too.
- If frontend websites are supported, provide an official way to run webpack.

# The Goal

The primary goal here is to learn, not to get adoption. Though, who knows how these things will pan out.


----------

[![hacktoberfest](https://hacktoberfest.digitalocean.com/assets/hacktoberfest-2018-social-card-c8d2e1489f647f2e0a26e6f598adeb760872818905b34cd437afc7ac2857ceab.png)](https://hacktoberfest.digitalocean.com/)
