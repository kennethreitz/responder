import jinja2

__all__ = ["Templates"]


class Templates:
    def __init__(
        self, directory="templates", autoescape=True, context=None, enable_async=False
    ):
        self.directory = directory
        loader = jinja2.FileSystemLoader([str(self.directory)])
        self._env = jinja2.Environment(
            loader=loader,
            autoescape=autoescape,  # noqa: S701
            enable_async=enable_async,
        )
        # A dedicated always-async environment for render_async. Previously a
        # single shared env had its ``is_async`` toggled per render_async call,
        # which raced concurrent sync/async renders. The two envs share one
        # globals dict so context updates apply to both.
        self._async_env = jinja2.Environment(
            loader=loader,
            autoescape=autoescape,  # noqa: S701
            enable_async=True,
        )
        self.default_context = {} if context is None else {**context}
        self._env.globals.update(self.default_context)
        self._async_env.globals = self._env.globals

    @property
    def context(self):
        return self._env.globals

    @context.setter
    def context(self, context):
        self._env.globals = {**self.default_context, **context}
        self._async_env.globals = self._env.globals

    def get_template(self, name):
        return self._env.get_template(name)

    def render(self, template, *args, **kwargs):
        """Renders the given `jinja2 <http://jinja.pocoo.org/docs/>`_ template, with provided values supplied.

        :param template: The filename of the jinja2 template.
        :param **kwargs: Data to pass into the template.
        :param **kwargs: Data to pass into the template.
        """  # noqa: E501
        return self.get_template(template).render(*args, **kwargs)

    async def render_async(self, template, *args, **kwargs):
        return await self._async_env.get_template(template).render_async(
            *args, **kwargs
        )

    def render_string(self, source, *args, **kwargs):
        """Renders the given `jinja2 <http://jinja.pocoo.org/docs/>`_ template string, with provided values supplied.

        :param source: The template to use.
        :param *args, **kwargs: Data to pass into the template.
        :param **kwargs: Data to pass into the template.
        """  # noqa: E501
        template = self._env.from_string(source)
        return template.render(*args, **kwargs)
