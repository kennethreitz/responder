Responder CLI
=============

Responder installs a command line program ``responder``. Use it to launch
a Responder application from a file or module, either located on a local
or remote filesystem, or object store.

Launch Module Entrypoint
------------------------

For loading a Responder application from a Python module, you will refer to
its ``API()`` instance using a `Python entry point object reference`_ that
points to a Python object. It is either in the form ``importable.module``,
or ``importable.module:object.attr``.

A basic invocation command to launch a Responder application:

.. code-block:: shell

    responder run acme.app

The command above assumes a Python package ``acme`` including an ``app``
module ``acme/app.py`` that includes an attribute ``api`` that refers
to a ``responder.API`` instance, reflecting the typical layout of
a standard Responder application.

Loading a Responder application using an entrypoint specification will
inherit the capacities of `Python's import system`_, as implemented by
`importlib`_.

Launch Local File
-----------------

Acquire a minimal example single-file application, ``helloworld.py`` [1]_,
to your local filesystem, giving you the chance to edit it, and launch the
Responder HTTP service.

.. code-block:: shell

    wget https://github.com/kennethreitz/responder/raw/refs/heads/main/examples/helloworld.py
    responder run helloworld.py

.. note::

    To validate the example application, invoke a HTTP request, for example using
    `curl`_, `HTTPie`_, or your favourite browser at hand.

    .. code-block:: shell

        http http://127.0.0.1:5042/Hello

    The response is no surprise.

    ::

        HTTP/1.1 200 OK
        content-length: 13
        content-type: text/plain
        date: Sat, 26 Oct 2024 13:16:55 GMT
        encoding: utf-8
        server: uvicorn

        Hello, world!

.. [1] The Responder application `helloworld.py`_ implements a basic echo handler.

Launch Remote File
------------------

You can also launch a single-file application where its Python file is stored
on a remote location after installing the ``cli-full`` extra.

.. code-block:: shell

    uv pip install 'responder[cli-full]'

Responder supports all filesystem adapters compatible with `fsspec`_, and
installs the adapters for Azure Blob Storage (az), Google Cloud Storage (gs),
GitHub, HTTP, and AWS S3 by default.

.. code-block:: shell

    # Works 1:1.
    responder run https://github.com/kennethreitz/responder/raw/refs/heads/main/examples/helloworld.py
    responder run github://kennethreitz:responder@/examples/helloworld.py

If you need access other kinds of remote targets, see the `list of
fsspec-supported filesystems and protocols`_. The next section enumerates
a few synthetic examples. The corresponding storage buckets do not even
exist, so don't expect those commands to work.

.. code-block:: shell

    # Azure Blob Storage, Google Cloud Storage, and AWS S3.
    responder run az://kennethreitz-assets/responder/examples/helloworld.py
    responder run gs://kennethreitz-assets/responder/examples/helloworld.py
    responder run s3://kennethreitz-assets/responder/examples/helloworld.py

    # Hadoop Distributed File System (hdfs), SSH File Transfer Protocol (sftp),
    # Common Internet File System (smb), Web-based Distributed Authoring and
    # Versioning (webdav).
    responder run hdfs://kennethreitz-assets/responder/examples/helloworld.py
    responder run sftp://user@host/kennethreitz/responder/examples/helloworld.py
    responder run smb://workgroup;user:password@server:port/responder/examples/helloworld.py
    responder run webdav+https://user:password@server:port/responder/examples/helloworld.py

.. tip::

    In order to install support for all filesystem types supported by fsspec, run:

    .. code-block:: shell

        uv pip install 'fsspec[full]'

    When using ``uv``, this concludes within an acceptable time of approx.
    25 seconds. If you need to be more selectively instead of using ``full``,
    choose from one or multiple of the available `fsspec extras`_, which are:

    abfs, arrow, dask, dropbox, fuse, gcs, git, github, hdfs, http, oci, s3,
    sftp, smb, ssh.

Launch with Non-Standard Instance Name
--------------------------------------

By default, Responder will acquire an ``responder.API`` instance using the
symbol name ``api`` from the specified Python module.

If your main application file uses a different name than ``api``, please
append the designated symbol name to the launch target address.

It works like this for module entrypoints and local files:

.. code-block:: shell

    responder run acme.app:service
    responder run /path/to/acme/app.py:service

It works like this for URLs:

.. code-block:: shell

    responder run http://app.server.local/path/to/acme/app.py#service

Within your ``app.py``, the instance would have been defined to use
the ``service`` symbol name instead of ``api``, like this:

.. code-block:: python

    service = responder.API()

Build JavaScript Application
----------------------------

The ``build`` subcommand invokes ``npm run build``, optionally accepting
a target directory. By default, it uses the current working directory,
where it expects a regular NPM ``package.json`` file.

.. code-block:: shell

    responder build

When specifying a target directory, Responder will change to that
directory beforehand.

.. code-block:: shell

    responder build /path/to/project


.. _curl: https://curl.se/
.. _fsspec: https://filesystem-spec.readthedocs.io/en/latest/
.. _fsspec extras: https://github.com/fsspec/filesystem_spec/blob/2024.12.0/pyproject.toml#L27-L69
.. _helloworld.py: https://github.com/kennethreitz/responder/blob/main/examples/helloworld.py
.. _HTTPie: https://httpie.io/docs/cli
.. _importlib: https://docs.python.org/3/library/importlib.html
.. _list of fsspec-supported filesystems and protocols: https://github.com/fsspec/universal_pathlib#currently-supported-filesystems-and-protocols
.. _Python entry point object reference: https://packaging.python.org/en/latest/specifications/entry-points/
.. _Python's import system: https://docs.python.org/3/reference/import.html
