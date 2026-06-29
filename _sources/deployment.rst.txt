Deployment
==========

Responder applications are standard `ASGI <https://asgi.readthedocs.io/>`_
apps. ASGI (Asynchronous Server Gateway Interface) is the modern successor
to WSGI — it supports async, WebSockets, and HTTP/2. This means you can
deploy a Responder app anywhere that runs Python, using any ASGI server.


Running Locally
---------------

During development, ``api.run()`` is all you need::

    if __name__ == "__main__":
        api.run()

This starts a `uvicorn <https://www.uvicorn.org/>`_ server on
``127.0.0.1:5042``. Uvicorn is a lightning-fast ASGI server built on
`uvloop <https://uvloop.readthedocs.io/>`_ — it handles thousands of
concurrent connections efficiently and protects against slowloris attacks,
making a reverse proxy like nginx optional for many deployments.


Docker
------

Docker is the most common way to package and deploy web applications.
Here's a minimal Dockerfile::

    FROM python:3.13-slim
    WORKDIR /app
    COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
    COPY . .
    RUN uv pip install --system responder
    ENV PORT=80
    EXPOSE 80
    CMD ["python", "api.py"]

Build and run::

    $ docker build -t myapi .
    $ docker run -p 8000:80 myapi

The ``python:3.13-slim`` image is about 150MB — small enough for fast
deploys but includes everything you need. Using ``uv`` for installs
is significantly faster than pip. For even smaller images, you can use
``python:3.13-alpine``, though some packages may need extra build
dependencies.


Cloud Platforms
---------------

Responder automatically honors the ``PORT`` environment variable. When
``PORT`` is set, the server binds to ``0.0.0.0`` on that port — this is
the convention that virtually every cloud platform uses.

This means zero configuration on:

- **Fly.io** — ``fly launch`` and you're done
- **Railway** — push your code, Railway sets ``PORT``
- **Render** — set start command to ``python api.py``
- **Google Cloud Run** — containerize and deploy
- **Azure Container Apps** — same pattern
- **AWS App Runner** — and here too

The pattern is always the same: deploy your code, set the start command
to ``python api.py``, and the platform handles the rest.


Health Check Endpoint
---------------------

Every production deployment needs a health check — a lightweight endpoint
that monitoring tools, load balancers, and orchestrators can poll to verify
your service is running::

    @api.route("/health", include_in_schema=False)
    def health(req, resp):
        resp.media = {"status": "healthy"}

Keep it simple. Don't query the database or do expensive work — the health
check should return instantly. Cloud platforms, Docker, and Kubernetes all
look for an HTTP 200 to confirm your service is alive.

In v5, Responder generates your :doc:`OpenAPI schema <tour>` from route
signatures, so every route is documented automatically. ``include_in_schema=False``
keeps this internal endpoint out of the public spec.

For Docker, add a ``HEALTHCHECK`` instruction::

    HEALTHCHECK --interval=30s --timeout=3s \
        CMD curl -f http://localhost/health || exit 1


Uvicorn Directly
----------------

For production deployments where you want more control, bypass
``api.run()`` and use uvicorn directly::

    $ uvicorn api:api --host 0.0.0.0 --port 8000 --workers 4

The ``--workers`` flag spawns multiple processes, each handling requests
independently. A good starting point is 2-4 workers per CPU core.

.. note::

   Each worker is a separate process. With the default ``sessions="auto"`` and
   no signing key set, every worker mints its **own** random key, so signed
   session cookies won't validate across workers and load-balanced users get
   logged out. Before running more than one worker or instance, set a stable
   ``RESPONDER_SECRET_KEY`` (or ``API(secret_key=...)``). For sessions that
   survive across separate machines, store them server-side with a shared
   backend such as ``AsyncRedisSessionBackend``. See the
   :doc:`configuration guide <guide-config>`.

Uvicorn supports many options — SSL certificates, access logging, graceful
shutdown timeouts, and more. See the
`uvicorn documentation <https://www.uvicorn.org/deployment/>`_ for details.

For platforms like Heroku or Railway that use a ``Procfile``::

    web: uvicorn api:api --host 0.0.0.0 --port $PORT --workers 4


Granian
-------

`Granian <https://github.com/emmett-framework/granian>`_ is a Rust-based HTTP
server that runs ASGI, WSGI, and RSGI apps from a single dependency. It's a
strong production peer to uvicorn — native HTTP/2, WebSockets enabled by
default, and no separate worker package to install.

Install it::

    $ uv pip install 'responder[server]'

Responder apps are ASGI, so run them with the ``asgi`` interface::

    $ granian --interface asgi api:api

The familiar host, port, and worker flags all apply::

    $ granian --interface asgi --host 0.0.0.0 --port 8000 --workers 4 api:api

To serve HTTP/2 in production, add ``--http 2``::

    $ granian --interface asgi --host 0.0.0.0 --port 8000 --workers 4 --http 2 api:api

For a ``Procfile``::

    web: granian --interface asgi --host 0.0.0.0 --port $PORT --workers 4 api:api

Like uvicorn's ``--workers``, every Granian worker is a separate process, so the
stable-secret-key note above applies here too. Note that ``api.run()`` always
uses uvicorn; Granian is an external server you point at your app.


Docker Compose
--------------

For local development with databases and other services, Docker Compose
ties everything together::

    # docker-compose.yml
    services:
      api:
        build: .
        ports:
          - "5042:80"
        environment:
          - PORT=80
          - DATABASE_URL=postgresql+asyncpg://user:pass@db/myapp
          - RESPONDER_SECRET_KEY=dev-only-not-for-production-32chars
        depends_on:
          - db

      db:
        image: docker.io/postgres:16-alpine
        environment:
          POSTGRES_USER: user
          POSTGRES_PASSWORD: pass
          POSTGRES_DB: myapp
        volumes:
          - pgdata:/var/lib/postgresql/data

    volumes:
      pgdata:

Run with ``docker compose up``. The API waits for ``db`` to start, then
connects using the ``DATABASE_URL`` environment variable.


Reverse Proxy
-------------

For high-traffic production deployments, you may want a reverse proxy like
`nginx <https://nginx.org/>`_ or `Caddy <https://caddyserver.com/>`_ in
front of your application for:

- **SSL/TLS termination** — let the proxy handle HTTPS certificates
- **Load balancing** — distribute traffic across multiple app instances
- **Static asset serving** — offload static files to the proxy
- **Rate limiting** — at the infrastructure level

A minimal Caddy config that handles HTTPS automatically::

    # Caddyfile
    example.com {
        reverse_proxy localhost:5042
    }

Responder's ``TrustedHostMiddleware`` and ``HTTPSRedirectMiddleware`` work
correctly behind proxies that set standard forwarding headers
(``X-Forwarded-For``, ``X-Forwarded-Proto``).

Behind a TLS-terminating proxy this is exactly right: in production
(``debug=False``) Responder marks the session cookie ``Secure`` by default, so
it only travels over HTTPS — no action needed. Only pass
``session_https_only=False`` if you genuinely serve plain HTTP. (Browsers reject
``SameSite=None`` without a Secure cookie, so Responder rejects that combination
too.)

That said, uvicorn and Granian are both production-ready on their own. Many
applications run the ASGI server directly without a reverse proxy and do just
fine.


Production Checklist
--------------------

Before going live:

- **Set a stable secret key** — pass ``API(secret_key=...)`` or set
  ``RESPONDER_SECRET_KEY`` (16+ chars; generate one with
  ``python -c "import secrets; print(secrets.token_urlsafe(32))"``). It must be
  stable across workers and restarts, or signed sessions stop validating. If
  your service is stateless and never touches ``req.session``, pass
  ``API(sessions=False)`` instead to skip sessions entirely. See the
  :doc:`configuration guide <guide-config>`.
- **Disable debug mode** — it's off by default; never set ``debug=True`` in production
- **Set allowed hosts** — ``allowed_hosts=[...]``, restricted to your domains
- **Use multiple workers** — ``--workers 4`` or more, depending on CPU cores
  (set a stable secret key first — see above)
- **Add a health check** — ``/health`` endpoint for monitoring
- **Enable HTTPS** — via your proxy, cloud platform, or your ASGI server's
  ``--ssl-*`` flags; the session cookie is then ``Secure`` automatically
- **Set up logging** — your ASGI server logs requests by default; pipe them to your log aggregator
- **Pin your dependencies** — use a lock file or pinned requirements for reproducible deploys
