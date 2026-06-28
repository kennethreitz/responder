Configuration
=============

Every application needs different settings for different environments —
debug mode in development, real secrets in production, different database
URLs for testing. This guide covers how to manage configuration cleanly.


Environment Variables
---------------------

The simplest and most universal approach. Environment variables work
everywhere — locally, in Docker, on cloud platforms — and keep secrets
out of your source code::

    import os
    import responder

    api = responder.API(
        # RESPONDER_SECRET_KEY is picked up from the environment automatically
        debug=os.getenv("DEBUG", "false").lower() == "true",
        cors=os.getenv("CORS_ENABLED", "false").lower() == "true",
    )

Some variables Responder reads automatically:

- ``PORT`` — when set, ``api.run()`` binds to ``0.0.0.0`` on this port
- ``RESPONDER_SECRET_KEY`` — the signing key for sessions, used when you
  don't pass ``secret_key=`` explicitly (see `Secret Key`_ below)

Set variables in your shell::

    $ export RESPONDER_SECRET_KEY="your-secret-here"
    $ export DEBUG=true
    $ python app.py

Or in a ``.env`` file (don't commit this to git)::

    RESPONDER_SECRET_KEY=your-secret-here
    DEBUG=true


Using .env Files
----------------

For local development, a ``.env`` file is convenient. Install
``python-dotenv`` and load it at the top of your app::

    $ uv pip install python-dotenv

::

    from dotenv import load_dotenv
    load_dotenv()

    import responder

    # Picks up RESPONDER_SECRET_KEY (and friends) from the loaded .env
    api = responder.API()

Add ``.env`` to your ``.gitignore`` — never commit secrets.


Configuration Class Pattern
----------------------------

For larger applications, a configuration class keeps things organized::

    import os

    class Config:
        SECRET_KEY = os.environ.get("RESPONDER_SECRET_KEY")  # None in dev → auto-generated
        DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
        DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///dev.db")
        CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "").split(",")

    config = Config()

    api = responder.API(
        debug=config.DEBUG,
        secret_key=config.SECRET_KEY,
        cors=bool(config.CORS_ORIGINS[0]),
        cors_params={"allow_origins": config.CORS_ORIGINS},
    )

This makes it easy to see all your settings in one place.


Secret Key
----------

The ``secret_key`` signs cookie-based session data. If someone knows your
secret key, they can forge sessions and impersonate any user, so it must
be a real random value and stay out of source control.

Responder resolves the key in this order:

1. the explicit ``secret_key=`` argument
2. the ``RESPONDER_SECRET_KEY`` environment variable
3. otherwise, under the default ``sessions="auto"``, a random key minted
   per process at startup (with a loud warning)

Generate one with::

    $ python -c "import secrets; print(secrets.token_urlsafe(32))"

Then supply it via the environment — no code required::

    $ export RESPONDER_SECRET_KEY="paste-the-generated-key-here"

or pass it explicitly::

    api = responder.API(secret_key=os.environ["RESPONDER_SECRET_KEY"])

A few rules worth knowing:

- **Set a stable key in production.** The auto-minted key (step 3) is
  different in every worker and is regenerated on restart, so signed
  session cookies stop validating across processes — users get logged
  out. It's fine for a single dev process, but any multi-worker or
  multi-instance deploy needs a fixed ``RESPONDER_SECRET_KEY``.
- **The old public default is rejected.** ``secret_key="NOTASECRET"``
  now raises ``SessionConfigError`` (a ``ValueError`` subclass from
  ``responder.ext.sessions``).
- **Use at least 16 characters.** Shorter keys are accepted but warn.
- **Rotate it** if it's ever compromised — this invalidates all existing
  sessions.

.. note::

   A server-side session backend stores data on the server and keeps only
   an opaque id in the cookie, so it doesn't sign anything — ``secret_key``
   is irrelevant in that mode. See `Sessions`_ below.


Sessions
--------

Sessions are enabled by default and secure out of the box. The
``sessions=`` knob controls them:

- ``"auto"`` (default) — cookie sessions on; auto-mints an ephemeral key
  if none is set (see `Secret Key`_).
- ``True`` — strict mode: requires a real key via ``secret_key=`` or
  ``RESPONDER_SECRET_KEY``, and raises ``SessionConfigError`` if none is
  found.
- ``False`` — disables session middleware entirely. Accessing
  ``req.session`` / ``resp.session`` then raises ``RuntimeError``. Use
  this for stateless APIs to silence the auto-key startup warning.

::

    api = responder.API(sessions=False)  # stateless service, no sessions

The cookie and lifetime are tunable:

- ``session_https_only`` (default ``None``) — marks the cookie ``Secure``
  in production (``debug=False``) and not under ``debug=True``. Behind a
  TLS-terminating proxy this needs no action; pass ``False`` only if you
  genuinely serve plain HTTP.
- ``session_same_site`` (default ``"lax"``) — ``"strict"``, ``"lax"``, or
  ``"none"``. ``"none"`` without a ``Secure`` cookie raises ``ValueError``.
- ``session_cookie`` (default ``None`` → ``"session"``) — the cookie name.
- ``session_max_age`` (default ``1209600``, i.e. 14 days) — lifetime in
  seconds.
- ``session_backend`` (default ``None``) — by default, session data is
  stored in a signed cookie. Pass a backend to keep data server-side
  instead, leaving only an opaque id in the cookie::

    from responder.ext.sessions import RedisSessionBackend

    api = responder.API(
        session_backend=RedisSessionBackend(url="redis://localhost:6379/0"),
        session_max_age=3600,  # 1 hour
    )

  Built-in backends are ``MemorySessionBackend`` (single-process, dev
  only), ``RedisSessionBackend``, and ``AsyncRedisSessionBackend``
  (preferred for async apps). With a backend, ``secret_key`` is unused.
  Pairing ``sessions=False`` with a backend raises ``ValueError``.

For reading and writing session data in handlers — and rotating the
session id on login with ``regenerate_session`` — see
:doc:`tutorial-auth`.


Debug Mode
----------

Debug mode controls error page behavior:

- **On** (``debug=True``): detailed error pages with tracebacks. Never
  use this in production — it exposes your source code.
- **Off** (``debug=False``): generic error pages. This is the default.

::

    api = responder.API(debug=True)  # development only

A common pattern is to read it from the environment::

    api = responder.API(debug=os.getenv("DEBUG") == "true")

Debug also relaxes session cookie security: with the default
``session_https_only=None``, session cookies are marked ``Secure`` in
production but not under ``debug=True`` (so they round-trip over plain
``http://`` while you develop). See `Sessions`_.


Allowed Hosts
-------------

In production, always set ``allowed_hosts`` to prevent Host header
attacks. This should match the domain names your application serves::

    api = responder.API(
        allowed_hosts=["example.com", "www.example.com"],
    )

In development, you can use ``["*"]`` (the default) or specific local
addresses::

    api = responder.API(allowed_hosts=["localhost", "127.0.0.1"])


Putting It All Together
-----------------------

A production-ready configuration setup::

    import os
    from dotenv import load_dotenv

    load_dotenv()

    import responder

    api = responder.API(
        # secret_key is read from RESPONDER_SECRET_KEY automatically
        debug=os.getenv("DEBUG", "false") == "true",
        allowed_hosts=os.getenv("ALLOWED_HOSTS", "*").split(","),
        cors=bool(os.getenv("CORS_ORIGINS")),
        cors_params={
            "allow_origins": os.getenv("CORS_ORIGINS", "").split(","),
            "allow_methods": ["GET", "POST", "PUT", "DELETE"],
        },
    )

With a ``.env`` file for local development::

    RESPONDER_SECRET_KEY=dev-secret-do-not-use-in-prod
    DEBUG=true
    ALLOWED_HOSTS=localhost,127.0.0.1
    CORS_ORIGINS=http://localhost:3000

And the same variables set properly in production (via your cloud
platform's dashboard, Docker secrets, or a secrets manager) — with a real
``RESPONDER_SECRET_KEY`` generated by ``secrets.token_urlsafe(32)``.

For running this app under a real server, see :doc:`deployment`.
