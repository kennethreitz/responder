Deploying Responder
===================

You can deploy Responder anywhere you can deploy a basic Python application.

Heroku Deployment
-----------------

The basics::

    $ mkdir my-api
    $ cd my-api
    $ git init
    $ heroku create
    ...

Install Responder::

    $ pipenv install responder
    ...

Write out a ``api.py``::

    import responder

    api = responder.API()

    @api.route("/")
    async def hello(req, resp):
        resp.text = "hello, world!"

    api.run()

Write out a ``Procfile``::

    web: python api.py

That's it! Next, we commit and push to Heroku::

    $ git add -A
    $ git commit -m 'initial commit'
    $ git push heroku master
