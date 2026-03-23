API Reference
=============

This page documents Responder's public Python API. For usage examples
and explanations, see the :doc:`quickstart` and :doc:`tour`.


The API Class
-------------

The central object of every Responder application. It holds your routes,
middleware, templates, and configuration. Create one at the top of your
module and use it to define your entire web service.

.. module:: responder

.. autoclass:: API
    :inherited-members:


Request
-------

The request object is passed into every view as the first argument. It
gives you access to everything the client sent — headers, query
parameters, the request body, cookies, and more.

Most properties are synchronous, but reading the body requires ``await``
because it involves I/O.

.. autoclass:: Request
    :inherited-members:


Response
--------

The response object is passed into every view as the second argument.
Mutate it to control what gets sent back to the client — the body,
status code, headers, and cookies.

.. autoclass:: Response
    :inherited-members:


Route Groups
------------

Group related routes under a shared URL prefix — useful for API versioning
and organizing large applications.

.. autoclass:: responder.api.RouteGroup
    :members:


Background Queue
----------------

Run tasks in background threads without blocking the response. Available
as ``api.background``.

.. autoclass:: responder.background.BackgroundQueue
    :members:


Query Dict
----------

A dictionary subclass for query string parameters with multi-value support.

.. autoclass:: responder.models.QueryDict
    :members:


Rate Limiter
------------

In-memory token bucket rate limiter. Limits requests per client IP address
and returns ``429 Too Many Requests`` when exceeded.

.. autoclass:: responder.ext.ratelimit.RateLimiter
    :members:


Status Code Helpers
-------------------

Convenience functions for checking which category a status code falls
into. Useful in middleware and after-request hooks.

.. autofunction:: responder.status_codes.is_100

.. autofunction:: responder.status_codes.is_200

.. autofunction:: responder.status_codes.is_300

.. autofunction:: responder.status_codes.is_400

.. autofunction:: responder.status_codes.is_500
