Command Line Interface
======================

Responder installs a ``responder`` command that lets you launch
applications from the terminal. You can point it at a Python module,
a local file, or even a URL — and it will find your ``API`` instance
and start serving.


Launching from a Module
-----------------------

The most common way to run a Responder application in production. Use
Python's standard dotted module path::

    $ responder run acme.app

This imports ``acme.app`` and looks for an attribute called ``api``
(a ``responder.API`` instance). It's the same import system Python
uses everywhere — your ``PYTHONPATH`` and virtual environment are
respected.


Launching from a File
---------------------

During development, you often have a single file you want to run::

    $ responder run helloworld.py

This loads the file directly and starts the server. Quick and easy for
prototyping and single-file applications.

You can test it with a simple HTTP request::

    $ curl http://127.0.0.1:5042/hello
    hello, world!


Launching from a URL
--------------------

Responder can fetch and run a Python file from any URL — great for
demos, sharing examples, and running code from GitHub::

    $ responder run https://github.com/kennethreitz/responder/raw/refs/heads/main/examples/helloworld.py

This also works with ``github://`` URLs and any filesystem protocol
supported by `fsspec <https://filesystem-spec.readthedocs.io/>`_::

    $ responder run github://kennethreitz:responder@/examples/helloworld.py

Cloud storage is supported too — Azure Blob Storage, Google Cloud
Storage, S3, HDFS, SFTP, and more. Install ``fsspec[full]`` for all
protocols::

    $ uv pip install 'fsspec[full]'


Custom Instance Names
---------------------

By default, Responder looks for an attribute called ``api``. If your
application uses a different name, specify it with a colon::

    $ responder run acme.app:service
    $ responder run myapp.py:application

For URLs, use a fragment::

    $ responder run https://example.com/app.py#service


Environment Variables
---------------------

Responder automatically reads the ``PORT`` environment variable at
runtime:

- ``PORT`` — bind to ``0.0.0.0`` on this port (cloud platform convention)

When ``PORT`` is set, the server binds to all interfaces automatically.
This is how cloud platforms like Fly.io, Railway, and Heroku inject the
listen port.

For other settings like ``SECRET_KEY``, read them in your application
code and pass them to ``responder.API()``::

    import os
    api = responder.API(secret_key=os.environ["SECRET_KEY"])


Building Frontend Assets
-------------------------

If your project includes a JavaScript frontend with a ``package.json``,
the ``build`` subcommand runs ``npm run build``::

    $ responder build
    $ responder build /path/to/frontend
