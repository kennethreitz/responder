"""Setting Templates.context must not wipe Jinja2's built-in globals."""

import asyncio

from responder.templates import Templates


def make_templates(tmp_path, **kwargs):
    return Templates(directory=str(tmp_path), **kwargs)


def test_context_setter_preserves_jinja_builtins(tmp_path):
    """range/namespace/cycler/joiner/lipsum/dict survive a context assignment."""
    templates = make_templates(tmp_path)
    templates.context = {"greeting": "hi"}

    for builtin in ("range", "namespace", "cycler", "joiner", "lipsum", "dict"):
        assert builtin in templates.context

    # A template using range() renders instead of raising UndefinedError.
    out = templates.render_string("{% for i in range(3) %}{{ i }}{% endfor %}")
    assert out == "012"


def test_context_setter_applies_values(tmp_path):
    templates = make_templates(tmp_path)
    templates.context = {"greeting": "hi"}
    assert templates.render_string("{{ greeting }}") == "hi"


def test_context_setter_keeps_default_context(tmp_path):
    templates = make_templates(tmp_path, context={"site": "responder"})
    templates.context = {"greeting": "hi"}
    assert templates.render_string("{{ site }}:{{ greeting }}") == "responder:hi"


def test_context_setter_replaces_previous_custom_keys(tmp_path):
    """Assignment semantics: stale custom keys from a prior set are dropped."""
    templates = make_templates(tmp_path)
    templates.context = {"old": "value"}
    templates.context = {"new": "value"}
    assert "new" in templates.context
    assert "old" not in templates.context
    # ...but the built-ins are still there.
    assert "range" in templates.context


def test_context_setter_restores_shadowed_builtin(tmp_path):
    """Regression: a user key shadowing a Jinja built-in (e.g. ``range``) is
    removed — and the built-in restored — when the context is reassigned."""
    templates = make_templates(tmp_path)

    templates.context = {"range": lambda *args: ["x"]}
    out = templates.render_string("{% for i in range(2) %}{{ i }}{% endfor %}")
    assert out == "x"  # the fake is in effect

    templates.context = {}
    out = templates.render_string("{% for i in range(2) %}{{ i }}{% endfor %}")
    assert out == "01"  # the real built-in is back
    assert templates.context["range"] is range


def test_context_setter_reshadow_then_unshadow_builtin(tmp_path):
    templates = make_templates(tmp_path)
    templates.context = {"range": lambda *args: ["x"]}
    templates.context = {"range": lambda *args: ["y", "y"]}  # still shadowed
    out = templates.render_string("{% for i in range(2) %}{{ i }}{% endfor %}")
    assert out == "yy"
    templates.context = {"greeting": "hi"}
    out = templates.render_string("{% for i in range(2) %}{{ i }}{% endfor %}")
    assert out == "01"


def test_context_setter_override_wins_over_default(tmp_path):
    templates = make_templates(tmp_path, context={"who": "default"})
    templates.context = {"who": "override"}
    assert templates.render_string("{{ who }}") == "override"


def test_context_setter_shares_globals_with_async_env(tmp_path):
    """The sync and async environments keep sharing one globals object."""
    templates = make_templates(tmp_path)
    templates.context = {"greeting": "hi"}
    assert templates._env.globals is templates._async_env.globals


def test_render_async_sees_updated_context_and_builtins(tmp_path):
    (tmp_path / "loop.html").write_text(
        "{{ greeting }}{% for i in range(2) %}{{ i }}{% endfor %}"
    )
    templates = make_templates(tmp_path)
    templates.context = {"greeting": "hi"}

    out = asyncio.run(templates.render_async("loop.html"))
    assert out == "hi01"


def test_render_with_builtins_without_context_set(tmp_path):
    """Pre-existing happy path: built-ins work when context is never assigned."""
    templates = make_templates(tmp_path)
    out = templates.render_string("{% for i in range(2) %}{{ i }}{% endfor %}")
    assert out == "01"
