Responder CLI
=============

Responder installs a command line program ``responder``. Use it to launch
a Responder application from a file or module.

Launch application from file
----------------------------

Acquire minimal example application, `helloworld.py`_,
implementing a basic echo handler, and launch the HTTP service.

.. code-block:: shell

    wget https://github.com/kennethreitz/responder/raw/refs/heads/main/examples/helloworld.py
    responder run helloworld.py

In another terminal, invoke a HTTP request, for example using `HTTPie`_.

.. code-block:: shell

    http http://127.0.0.1:5042/hello

The response is no surprise.

::

    HTTP/1.1 200 OK
    content-length: 13
    content-type: text/plain
    date: Sat, 26 Oct 2024 13:16:55 GMT
    encoding: utf-8
    server: uvicorn

    hello, world!


Launch application from module
------------------------------

If your Responder application has been implemented as a Python module,
launch it like this:

.. code-block:: shell

    responder run acme.app

That assumes a Python package ``acme`` including an ``app`` module
``acme/app.py`` that includes an attribute ``api`` that refers
to a ``responder.API`` instance, reflecting the typical layout of
a standard Responder application.

.. rubric:: Non-standard instance name

When your attribute that references the ``responder.API`` instance
is called differently than ``api``, append it to the launch target
address like this:

.. code-block:: shell

    responder run acme.app:service

Within your ``app.py``, the instance would have been defined like this:

.. code-block:: python

    service = responder.API()


Build JavaScript application
----------------------------

The ``build`` subcommand invokes ``npm run build``, optionally accepting
a target directory. By default, it uses the current working directory,
where it expects a regular NPM ``package.json`` file.

.. code-block:: shell

    responder build

When specifying a target directory, responder will change to that
directory beforehand.

.. code-block:: shell

    responder build /path/to/project


.. _helloworld.py: https://github.com/kennethreitz/responder/blob/main/examples/helloworld.py
.. _HTTPie: https://httpie.io/docs/cli
