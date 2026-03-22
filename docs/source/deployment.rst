Deployment
==========

Docker
------

::

    FROM python:3.13-slim
    WORKDIR /app
    COPY . .
    RUN pip install responder
    ENV PORT=80
    EXPOSE 80
    CMD ["python", "api.py"]


Cloud Platforms
---------------

Responder honors the ``PORT`` environment variable automatically.
It works with any platform that sets ``PORT``: Fly.io, Railway, Render,
Google Cloud Run, etc.


Uvicorn Directly
----------------

For more control, run with uvicorn::

    uvicorn api:api --host 0.0.0.0 --port 8000 --workers 4
