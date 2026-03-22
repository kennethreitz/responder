Responder
=========

A familiar HTTP Service Framework for Python, powered by `Starlette`_.

.. code:: python

   import responder

   api = responder.API()

   @api.route("/{greeting}")
   async def greet_world(req, resp, *, greeting):
       resp.text = f"{greeting}, world!"

   if __name__ == '__main__':
       api.run()

Install it::

    pip install responder

Python 3.9+.

.. toctree::
   :maxdepth: 2

   quickstart
   tour
   deployment
   testing
   api
   cli

.. toctree::
   :maxdepth: 1
   :caption: Project

   changes
   Sandbox <sandbox>
   backlog


.. _Starlette: https://www.starlette.io/
