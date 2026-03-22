Deploying Responder
===================

You can deploy Responder anywhere you can deploy a basic Python application.

Docker Deployment
-----------------

Assuming an existing ``api.py`` containing your Responder application.

``Dockerfile``::

    FROM python:3.13-slim
    WORKDIR /app
    COPY . .
    RUN pip install responder
    ENV PORT=80
    EXPOSE 80
    CMD ["python", "api.py"]

That's it!

Cloud Deployment
----------------

Responder automatically honors the ``PORT`` environment variable, which is
set by most cloud platforms (Fly.io, Railway, Render, Google Cloud Run, etc.).

The basics::

    $ mkdir my-api
    $ cd my-api

Write out an ``api.py``::

    import responder

    api = responder.API()

    @api.route("/")
    async def hello(req, resp):
        resp.text = "hello, world!"

    if __name__ == "__main__":
        api.run()

Deploy with your platform of choice. Responder will bind to ``0.0.0.0``
on the port specified by ``PORT`` automatically.

Running with Uvicorn Directly
-----------------------------

For production deployments, you can also run your app directly with uvicorn::

    uvicorn api:api --host 0.0.0.0 --port 8000 --workers 4
