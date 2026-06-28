# Sphinx configuration for Responder documentation.

import os

project = "responder"
copyright = "2018-2026, Kenneth Reitz"
author = "Kenneth Reitz"

here = os.path.abspath(os.path.dirname(__file__))
about = {}
with open(os.path.join(here, "..", "..", "responder", "__version__.py")) as f:
    exec(f.read(), about)

version = about["__version__"]
release = about["__version__"]

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
    "myst_parser",
    "sphinx_copybutton",
    "sphinx_design_elements",
]

templates_path = ["_templates"]
source_suffix = {".rst": "restructuredtext"}
master_doc = "index"
language = "en"
exclude_patterns = []

# Theme
html_theme = "alabaster"
html_theme_options = {
    "show_powered_by": False,
    "github_user": "kennethreitz",
    "github_repo": "responder",
    "github_banner": False,
    "show_related": False,
    "sidebar_width": "240px",
    "page_width": "1000px",
    # Keep the full page tree visible in the sidebar; the current page expands
    # to show its sections, every other page stays a one-line link.
    "sidebar_collapse": True,
}
html_static_path = ["_static"]
# Every page gets the project intro, the full cross-page navigation tree, the
# current page's sections (via the expanded navigation), prev/next links, and
# search — so you can always jump anywhere from anywhere.
html_sidebars = {
    "**": [
        "sidebarintro.html",
        "navigation.html",
        "relations.html",
        "searchbox.html",
    ],
}

# MyST
myst_heading_anchors = 3

# Copybutton
copybutton_remove_prompts = True
copybutton_prompt_text = r">>> |\.\.\. |\$ "
copybutton_prompt_is_regexp = True
