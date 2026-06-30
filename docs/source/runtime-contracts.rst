Runtime Contracts
=================

Responder keeps a small set of runtime contracts stable across the 7.x series.
This page collects the behaviors that application code, tests, generated
clients, and OpenAPI consumers can rely on.


Error Responses
---------------

Framework-generated errors use ``application/problem+json`` by default. The
payload always includes:

- ``type``: ``"about:blank"`` unless an application ``problem_handler`` changes
  it.
- ``title``: the HTTP status title.
- ``status``: the numeric HTTP status code.

When extra context exists, the payload may also include:

- ``detail`` for human-readable context.
- ``errors`` for structured validation failures.
- ``request_id`` when request ID middleware or structured logging has attached
  one to the request scope.

The standard framework error statuses are:

.. list-table::
   :header-rows: 1

   * - Status
     - Default title
     - Notes
   * - ``400``
     - ``Bad Request``
     - Malformed request bodies, such as invalid JSON.
   * - ``401``
     - ``Unauthorized``
     - Authentication failed or credentials were missing.
   * - ``403``
     - ``Forbidden``
     - Authentication succeeded, but the principal lacks required scope.
   * - ``404``
     - ``Not Found``
     - No route matched the request path.
   * - ``405``
     - ``Method Not Allowed``
     - The path exists but does not accept the request method.
   * - ``413``
     - ``Content Too Large``
     - ``max_request_size`` rejected the body.
   * - ``422``
     - ``Validation Error``
     - Path, query, header, cookie, form, file, or body validation failed.
   * - ``500``
     - ``Internal Server Error``
     - Unhandled application errors and response-model validation failures.
   * - ``504``
     - ``Gateway Timeout``
     - ``request_timeout`` expired before the handler finished.

Pass ``API(problem_details=False)`` to keep the legacy behavior. In that mode,
requests that ask for JSON receive ``{"error": "..."}``, while validation
failures receive ``{"errors": [...]}``. Requests that do not ask for JSON may
receive plain text, matching older Responder releases.

``API(problem_handler=...)`` can enrich or replace problem-details payloads. The
handler receives ``(payload, request, exc)``; returning ``None`` means the
payload was mutated in place. If the handler raises or returns a non-dict value,
Responder logs the failure and uses the original framework payload.


Authentication
--------------

Application-level auth configured with ``API(auth=...)`` applies to every route
by default. A route can opt out with ``auth=None`` or replace it with
``auth=...`` on the route decorator.

Auth helpers inject the authenticated principal as ``user``, ``principal``, and
``auth`` when the route declares those keyword-only parameters. They also set
``req.state.user`` and ``req.state.auth``.

``auth.optional()`` only treats missing credentials as anonymous. Malformed
credentials, wrong schemes, bad API keys, and failed verification still fail
through the wrapped auth scheme. For scoped auth, anonymous requests are allowed
when credentials are missing, but supplied credentials must still satisfy the
required scopes.

Scoped auth returns ``403`` when a principal is authenticated but does not hold
every required scope or role. If the wrapped scheme provides a challenge,
Responder adds an ``insufficient_scope`` ``WWW-Authenticate`` challenge with the
missing scopes.

``api.policy(name, auth)`` creates a named wrapper around an auth helper. The
policy name is an application label only: authentication, principal injection,
OpenAPI security schemes, optional auth, and scoped requirements all continue to
come from the wrapped helper.


Dependency Injection
--------------------

Responder resolves route dependencies after before-request hooks and auth, and
before the route handler. For HTTP requests, the order is:

1. Before-request hooks.
2. Route or application auth.
3. Route dependency guards.
4. Handler parameter dependencies.
5. Handler.
6. After-request hooks.
7. Request-scoped dependency teardown.

Each dependency is resolved at most once per request. Shared sub-dependencies
reuse the same value within that request.

Generator dependencies tear down in reverse dependency order. Teardown failures
are logged, and remaining teardowns still run.

App-scoped dependencies are resolved once for the application lifetime and are
protected by a lock during first resolution, so concurrent first users share the
same initialized value. If first initialization raises, the failed value is not
cached and a later request may retry. App-scoped teardowns run during lifespan
shutdown and clear the app-scope cache.

App-scoped dependencies may depend on other app-scoped dependencies, but they
cannot receive the request object and cannot depend on request-scoped
dependencies.


Response Models
---------------

``response_model=...`` validates the response body after the handler has run.
Valid responses are coerced and filtered through the model. Invalid responses
fail closed:

- In normal mode, the client receives a ``500`` framework error.
- In debug mode, the validation exception is raised for the developer.

Response-model validation failures are passed to ``problem_handler`` as the
``exc`` argument when problem details are enabled.


OpenAPI Defaults
----------------

When OpenAPI is enabled, Responder generates operation IDs, summaries, tags,
request bodies, response schemas, validation responses, auth security
requirements, and common framework error responses from the route contract.
Route decorators can add or override operation metadata with ``responses=``,
``examples=``, ``response_examples=``, and ``openapi_extra=``; nested response
metadata is deep-merged with the generated contract.

With problem details enabled, generated operations document
``application/problem+json`` responses and the reusable ``ProblemDetails``
component schema. With ``problem_details=False``, generated operations document
the legacy JSON error shape and do not register ``ProblemDetails``.

Generated OpenAPI documents are validated in the test suite for both OpenAPI
3.0.x and 3.1.x.
