"""Path-parameter names from an (untrusted) OpenAPI spec must be escaped when
interpolated into generated client string literals.

A name containing a quote/backslash/newline previously broke out of the
single-quoted `.replace('{name}', ...)` / `.gsub(...)` literal, producing a
client that fails to parse. We feed a hostile spec and check the output is
well-formed.
"""

from responder.ext.clientgen import _js_string, _ruby_string, generate_client

# A path-parameter name loaded with characters that break naive string
# interpolation: single quote, backslash, and a newline.
NASTY = "a'b\\c\nd"


def _spec():
    return {
        "openapi": "3.0.2",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/x/{" + NASTY + "}": {
                "get": {
                    "operationId": "get_x",
                    "parameters": [
                        {
                            "name": NASTY,
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }


def test_python_client_compiles_with_hostile_path_param():
    src = generate_client(_spec(), language="python")
    # Would raise SyntaxError before the fix (unescaped quote/newline in literal).
    compile(src, "<generated>", "exec")


def test_javascript_placeholder_is_escaped():
    src = generate_client(_spec(), language="javascript")
    # The placeholder is emitted through the JS string escaper, so the quote
    # can't break out of the literal in the generated `.replace(...)` call.
    placeholder = _js_string("{" + NASTY + "}")
    assert f".replace({placeholder}, quote(" in src


def test_ruby_placeholder_is_escaped():
    src = generate_client(_spec(), language="ruby")
    placeholder = _ruby_string("{" + NASTY + "}")
    assert f".gsub({placeholder}, quote(" in src


def test_typescript_client_still_generates():
    # TS shares the JS emitter; just ensure it doesn't blow up.
    src = generate_client(_spec(), language="typescript")
    assert "get_x" in src or "getX" in src
