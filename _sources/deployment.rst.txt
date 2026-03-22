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
    COPY . .
    RUN pip install responder
    ENV PORT=80
    EXPOSE 80
    CMD ["python", "api.py"]

Build and run::

    $ docker build -t myapi .
    $ docker run -p 8000:80 myapi

The ``python:3.13-slim`` image is about 150MB — small enough for fast
deploys but includes everything you need. For even smaller images, you
can use ``python:3.13-alpine``, though some packages may need extra
build dependencies.


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


Reverse Proxy
-------------

For high-traffic production deployments, you may want a reverse proxy like
`nginx <https://nginx.org/>`_ or `Caddy <https://caddyserver.com/>`_ in
front of your application for:

- **SSL/TLS termination** — let the proxy handle HTTPS certificates
- **Load balancing** — distribute traffic across multiple app instances
- **Static asset serving** — offload static files to the proxy
- **Rate limiting** — at the infrastructure level

Responder's ``TrustedHostMiddleware`` and ``HTTPSRedirectMiddleware`` work
correctly behind proxies that set standard forwarding headers
(``X-Forwarded-For``, ``X-Forwarded-Proto``).

That said, uvicorn is production-ready on its own. Many applications run
uvicorn directly without a reverse proxy and do just fine.
