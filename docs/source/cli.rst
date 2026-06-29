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
demos, sharing examples, and running code from GitHub. Remote targets
require the ``cli`` extra::

    $ uv pip install 'responder[cli]'

::

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


Run Options
-----------

A few flags tune how ``run`` behaves::

    $ responder run --debug acme.app

- ``--debug`` — turn on debug mode with verbose, debug-level logging.
- ``--limit-max-requests=<n>`` — serve at most ``n`` requests, then exit.
  Handy for smoke tests and short-lived runs.

To check the version or list every command, run::

    $ responder --version
    $ responder --help


Environment Variables
---------------------

Responder reads two environment variables on its own — convenient when
you launch through the CLI and would rather not touch application code:

- ``PORT`` — when set, the server binds to ``0.0.0.0`` on this port.
  This is how cloud platforms like Fly.io, Railway, and Heroku inject
  the listen port.
- ``RESPONDER_SECRET_KEY`` — the signing key for cookie sessions.

Set ``RESPONDER_SECRET_KEY`` for any real deployment. If it's unset (and
sessions are left at their ``"auto"`` default), Responder mints a random
key per process and logs a warning. That's fine for a quick demo, but
every worker ends up with a different key, so signed session cookies stop
validating across workers and restarts — and users get logged out.

Generate a stable key with the canonical one-liner::

    $ python -c "import secrets; print(secrets.token_urlsafe(32))"

then export it and run::

    $ export RESPONDER_SECRET_KEY="<paste-the-generated-key>"
    $ responder run acme.app

No code change is needed. To read a differently-named variable instead,
pass it explicitly when you construct the app::

    import os
    api = responder.API(secret_key=os.environ["MY_APP_SECRET"])

.. note::

    The old public default ``secret_key="NOTASECRET"`` is now rejected
    and raises ``SessionConfigError``. See :doc:`deployment` for the
    full production checklist and :doc:`guide-config` for the rest of
    the session settings.


Building Frontend Assets
-------------------------

If your project includes a JavaScript frontend with a ``package.json``,
the ``build`` subcommand runs ``npm run build``::

    $ responder build
    $ responder build /path/to/frontend


Generating a Client
-------------------

The ``client`` subcommand generates an API client from your app's OpenAPI
schema, using the same import target as ``run``. By default it prints a
Python client to stdout::

    $ responder client acme.app:api > clients/service.py

Use ``--lang`` for another ecosystem (``python``, ``javascript``,
``typescript``, ``ruby``, or ``php``), ``--class-name`` to name the class, and
``--output`` / ``-o`` to write a file instead of stdout::

    $ responder client --lang typescript --class-name ServiceClient \
        -o clients/service.ts acme.app:api

The generated clients are dependency-free and typed where your OpenAPI schema
has models. See :doc:`clientgen` for the full feature set and the
``api.generate_client(...)`` equivalent.
