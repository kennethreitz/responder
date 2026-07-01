import jinja2
from jinja2.defaults import DEFAULT_NAMESPACE

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
        # Keys we have laid over the environment globals; the context setter
        # uses this to undo a previous assignment (including restoring any
        # Jinja built-in a user key shadowed).
        self._context_keys: set[str] = set(self.default_context)
        self._env.globals.update(self.default_context)
        self._async_env.globals = self._env.globals

    @property
    def context(self):
        return self._env.globals

    @context.setter
    def context(self, context):
        # Update the globals dict in place rather than replacing it: this
        # preserves Jinja's built-in globals (``range``, ``namespace``,
        # ``cycler``, ...) and keeps the object identity shared with the
        # async environment (see ``__init__``).
        merged = {**self.default_context, **context}
        env_globals = self._env.globals
        for key in list(env_globals):
            if key not in DEFAULT_NAMESPACE and key not in merged:
                del env_globals[key]
        # Undo keys a previous assignment introduced that shadowed a Jinja
        # built-in (the loop above skips DEFAULT_NAMESPACE names): restore
        # the built-in unless the new mapping shadows it again.
        for key in self._context_keys:
            if key in DEFAULT_NAMESPACE and key not in merged:
                env_globals[key] = DEFAULT_NAMESPACE[key]
        self._context_keys = set(merged)
        env_globals.update(merged)

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
