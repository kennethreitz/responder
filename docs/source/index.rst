.. responder documentation master file, created by
   sphinx-quickstart on Thu Oct 11 12:58:34 2018.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

A familiar HTTP Service Framework
=================================

|Build Status| |image1| |image2| |image3| |image4| |image5|

.. |Build Status| image:: https://travis-ci.org/kennethreitz/responder.svg?branch=master
   :target: https://travis-ci.org/kennethreitz/responder
.. |image1| image:: https://img.shields.io/pypi/v/responder.svg
   :target: https://pypi.org/project/responder/
.. |image2| image:: https://img.shields.io/pypi/l/responder.svg
   :target: https://pypi.org/project/responder/
.. |image3| image:: https://img.shields.io/pypi/pyversions/responder.svg
   :target: https://pypi.org/project/responder/
.. |image4| image:: https://img.shields.io/github/contributors/kennethreitz/responder.svg
   :target: https://github.com/kennethreitz/responder/graphs/contributors
.. |image5| image:: https://img.shields.io/badge/Say%20Thanks-!-1EAEDB.svg
   :target: https://saythanks.io/to/kennethreitz

The Python world certainly doesn't need more web frameworks. But, it does need more creativity, so I thought I'd
spread some `Hacktoberfest <https://hacktoberfest.digitalocean.com/>`_ spirit around, bring some of my ideas to the table, and see what I could come up with.

An Example Web Service:
-----------------------

.. code:: python

   import responder

   api = responder.API()

   @api.route("/{greeting}")
   async def greet_world(req, resp, *, greeting):
       resp.text = f"{greeting}, world!"

   if __name__ == '__main__':
       api.run()

That ``async`` declaration is optional.

This gets you a ASGI app, with a production static files server
pre-installed, jinja2 templating (without additional imports), and a
production webserver based on uvloop, serving up requests with gzip
compression automatically.

Testimonials
------------

   “Pleasantly very taken with python-responder.
   `@kennethreitz <https://twitter.com/kennethreitz>`_ at his absolute
   best.” —Rudraksh M.K.

..

   “Buckle up!” —Tom Christie of `APIStar`_ and `Django REST Framework`_

   “I love that you are exploring new patterns. Go go go!” — Danny
   Greenfield, author of `Two Scoops of Django`_

..

   “Love what I have seen while it’s in progress! Many features of
   Responder are from my wishlist for Flask, and it’s even faster and
   even easier than Flask!” — Luna C.


..


   “The most ambitious crossover event in history.” —Pablo Cabezas, `on
   Tom Christie joining the project`_

.. _APIStar: https://github.com/encode/apistar
.. _Django REST Framework: https://www.django-rest-framework.org/
.. _Two Scoops of Django:
.. _on Tom Christie joining the project: https://twitter.com/pabloteleco/status/1050841098321620992?s=20

User Guides
-----------

.. toctree::
   :maxdepth: 2

   quickstart
   tour
   api


Installing Responder
====================

Install the latest release:

.. code-block:: shell

    $ pipenv install responder
    ✨🍰✨


Or, install from the development branch:

.. code-block:: shell

    $ pipenv install -e git+https://github.com/kennethreitz/responder.git#egg=responder


Only **Python 3.6+** is supported.



Web Service Performance Characteristics
---------------------------------------

The objective of these benchmark tests is not testing deployment (like uwsgi vs gunicorn vs uvicorn etc) but instead test the performance of python-response against other popular Python web frameworks.

Methodology
~~~~~~~~~~~

The results below were gotten running the performance tests on a Lenovo
W530, Intel(R) Core(TM) i7-3740QM CPU @ 2.70GHz, MEM: 32GB, Linux Mint
19. I used Python 3.7.0 with the WRK utility with params: wrk -d20s -t10
-c200 (i.e. 10 threads and 200 connections).

1. .. rubric:: Simple “Hello World” benchmark
      :name: simple-hello-world-benchmark

   | python-responder v0.0.1 (Master branch)
   | Requests/sec: 1368.23
   | Transfer/sec: 163.01KB

   | Django v2.1.2 (i18n == False)
   | Requests/sec: 544.54
   | Transfer/sec: 103.18KB

   | Django v2.1.2 (i18n == True)
   | Requests/sec: 535.12
   | Transfer/sec: 101.38KB

   | Django v2.1.2 (Minimal 1 file Django Application)
   | https://gist.github.com/aitoehigie/ebcc1d3e460e66cd51e5501fa2636798
   | Requests/sec: 701.53
   | Transfer/sec: 99.34KB

   | Flask v1.0.2
   | Requests/sec: 896.24
   | Transfer/sec: 144.41KB
The Basic Idea
--------------

The primary concept here is to bring the niceties that are brought forth from both Flask and Falcon and unify them into a single framework, along with some new ideas I have. I also wanted to take some of the API primitives that are instilled in the Requests library and put them into a web framework. So, you'll find a lot of parallels here with Requests.

- Setting ``resp.text`` sends back unicode, while setting ``resp.content`` sends back bytes.
- Setting ``resp.media`` sends back JSON/YAML (``.text``/``.content`` override this).
- Case-insensitive ``req.headers`` dict (from Requests directly).
- ``resp.status_code``, ``req.method``, ``req.url``, and other familiar friends.

Ideas
-----

- Flask-style route expression, with new capabilities -- all while using Python 3.6+'s new f-string syntax.
- I love Falcon's "every request and response is passed into to each view and mutated" methodology, especially ``response.media``, and have used it here. In addition to supporting JSON, I have decided to support YAML as well, as Kubernetes is slowly taking over the world, and it uses YAML for all the things. Content-negotiation and all that.
- **A built in testing client that uses the actual Requests you know and love**.
- The ability to mount other WSGI apps easily.
- Automatic gzipped-responses.
- In addition to Falcon's ``on_get``, ``on_post``, etc methods, Responder features an ``on_request`` method, which gets called on every type of request, much like Requests.
- A production static files server is built-in.
- Uvicorn built-in as a production web server. I would have chosen Gunicorn, but it doesn't run on Windows. Plus, Uvicorn serves well to protect against slowloris attacks, making nginx unneccessary in production.
- GraphQL support, via Graphene. The goal here is to have any GraphQL query exposable at any route, magically.


Future Ideas
------------

- Cookie-based sessions are currently an afterthought, as this is an API framework, but websites are APIs too.
- If frontend websites are supported, provide an official way to run webpack.



Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
