from contextlib import contextmanager

import jinja2


class Templates:
    def __init__(
        self, directory="templates", autoescape=True, context=None, enable_async=False
    ):
        self.directory = directory
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader([str(self.directory)]),
            autoescape=autoescape,
            enable_async=enable_async,
        )
        self.default_context = {} if context is None else {**context}
        self._env.globals.update(self.default_context)

    @property
    def context(self):
        return self._env.globals

    @context.setter
    def context(self, context):
        self._env.globals = {**self.default_context, **context}

    def get_template(self, name):
        return self._env.get_template(name)

    def render(self, template, *args, **kwargs):
        """Renders the given `jinja2 <http://jinja.pocoo.org/docs/>`_ template, with provided values supplied.

        :param template: The filename of the jinja2 template.
        :param **kwargs: Data to pass into the template.
        :param **kwargs: Data to pass into the template.
        """
        return self.get_template(template).render(*args, **kwargs)

    @contextmanager
    def _async(self):
        self._env.is_async = True
        try:
            yield
        finally:
            self._env.is_async = False

    async def render_async(self, template, *args, **kwargs):
        with self._async():
            return await self.get_template(template).render_async(*args, **kwargs)

    def render_string(self, source, *args, **kwargs):
        """Renders the given `jinja2 <http://jinja.pocoo.org/docs/>`_ template string, with provided values supplied.

        :param source: The template to use.
        :param *args, **kwargs: Data to pass into the template.
        :param **kwargs: Data to pass into the template.
        """
        template = self._env.from_string(source)
        return template.render(*args, **kwargs)
