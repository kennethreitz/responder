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
        debug=os.getenv("DEBUG", "false").lower() == "true",
        secret_key=os.environ["SECRET_KEY"],
        cors=os.getenv("CORS_ENABLED", "false").lower() == "true",
    )

Some variables Responder handles automatically:

- ``PORT`` — when set, the server binds to ``0.0.0.0`` on this port

Set variables in your shell::

    $ export SECRET_KEY="your-secret-here"
    $ export DEBUG=true
    $ python app.py

Or in a ``.env`` file (don't commit this to git)::

    SECRET_KEY=your-secret-here
    DEBUG=true


Using .env Files
----------------

For local development, a ``.env`` file is convenient. Install
``python-dotenv`` and load it at the top of your app::

    $ uv pip install python-dotenv

::

    from dotenv import load_dotenv
    load_dotenv()

    import os
    import responder

    api = responder.API(
        secret_key=os.environ["SECRET_KEY"],
    )

Add ``.env`` to your ``.gitignore`` — never commit secrets.


Configuration Class Pattern
----------------------------

For larger applications, a configuration class keeps things organized::

    import os

    class Config:
        SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret")
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

The ``secret_key`` is used to sign session cookies. If someone knows your
secret key, they can forge session data and impersonate any user.

Rules:

- **Never use the default** in production
- **Generate a random key**: ``python -c "import secrets; print(secrets.token_hex(32))"``
- **Store it in an environment variable**, not in code
- **Rotate it** if it's ever compromised (this invalidates all sessions)

::

    api = responder.API(secret_key=os.environ["SECRET_KEY"])


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
        debug=os.getenv("DEBUG", "false") == "true",
        secret_key=os.environ["SECRET_KEY"],
        allowed_hosts=os.getenv("ALLOWED_HOSTS", "*").split(","),
        cors=bool(os.getenv("CORS_ORIGINS")),
        cors_params={
            "allow_origins": os.getenv("CORS_ORIGINS", "").split(","),
            "allow_methods": ["GET", "POST", "PUT", "DELETE"],
        },
    )

With a ``.env`` file for local development::

    SECRET_KEY=dev-secret-do-not-use-in-prod
    DEBUG=true
    ALLOWED_HOSTS=localhost,127.0.0.1
    CORS_ORIGINS=http://localhost:3000

And environment variables set properly in production (via your cloud
platform's dashboard, Docker secrets, or a secrets manager).
