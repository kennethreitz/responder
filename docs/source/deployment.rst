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

    @api.route("/health")
    def health(req, resp):
        resp.media = {"status": "healthy"}

Keep it simple. Don't query the database or do expensive work — the health
check should return instantly. Cloud platforms, Docker, and Kubernetes all
look for an HTTP 200 to confirm your service is alive.

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

Uvicorn supports many options — SSL certificates, access logging, graceful
shutdown timeouts, and more. See the
`uvicorn documentation <https://www.uvicorn.org/deployment/>`_ for details.

For platforms like Heroku or Railway that use a ``Procfile``::

    web: uvicorn api:api --host 0.0.0.0 --port $PORT --workers 4


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
          - SECRET_KEY=dev-secret
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

That said, uvicorn is production-ready on its own. Many applications run
uvicorn directly without a reverse proxy and do just fine.


Production Checklist
--------------------

Before going live:

- **Set a secret key** — ``SECRET_KEY`` env var, never the default
- **Disable debug mode** — ``DEBUG=false`` or omit it entirely
- **Set allowed hosts** — restrict to your actual domain names
- **Use multiple workers** — ``--workers 4`` or more, depending on CPU cores
- **Add a health check** — ``/health`` endpoint for monitoring
- **Enable HTTPS** — via your proxy, cloud platform, or uvicorn's ``--ssl-*`` flags
- **Set up logging** — uvicorn logs requests by default; pipe them to your log aggregator
- **Pin your dependencies** — use a lock file or pinned requirements for reproducible deploys
