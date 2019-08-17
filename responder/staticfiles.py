from whitenoise import WhiteNoise


def _notfound_wsgi_app(environ, start_response):
    start_response("404 NOT FOUND", [("Content-Type", "text/plain")])
    return [b"Not Found."]


class StaticFiles:
    def __init__(self, directory=None, mkdir=True):
        self.directory = directory
        self.app = WhiteNoise(_notfound_wsgi_app, root=self.directory)

    def __call__(self, environ, start_response):
        return self.app(environ, start_response)


from starlette.staticfiles import StaticFiles
