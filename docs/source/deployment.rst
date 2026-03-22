Deployment
==========

Responder applications are standard ASGI apps. You can deploy them anywhere
you'd deploy a Python web service.


Running Locally
---------------

The simplest way to run your application::

    # api.py
    import responder

    api = responder.API()

    @api.route("/")
    def hello(req, resp):
        resp.text = "hello, world!"

    if __name__ == "__main__":
        api.run()

This starts a production uvicorn server on ``127.0.0.1:5042``.


Docker
------

A minimal Dockerfile for deploying a Responder application::

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


Cloud Platforms
---------------

Responder automatically honors the ``PORT`` environment variable, which is
set by most cloud platforms. When ``PORT`` is set, Responder binds to
``0.0.0.0`` on that port automatically.

This works out of the box with:

- **Fly.io**
- **Railway**
- **Render**
- **Google Cloud Run**
- **Azure Container Apps**
- **AWS App Runner**

Just deploy your code and set the start command to ``python api.py``.


Uvicorn Directly
----------------

For more control over the production server, you can bypass ``api.run()``
and use uvicorn directly::

    $ uvicorn api:api --host 0.0.0.0 --port 8000 --workers 4

This gives you access to all of uvicorn's options: worker count, SSL
certificates, access logging, and more. See the
`uvicorn documentation <https://www.uvicorn.org/>`_ for details.


Reverse Proxy
-------------

In production, you may want to place Responder behind a reverse proxy like
nginx or Caddy for SSL termination, load balancing, or serving static assets.

Responder's ``TrustedHostMiddleware`` and ``HTTPSRedirectMiddleware`` work
correctly behind proxies that set standard forwarding headers.
