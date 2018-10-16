"""Responder.

Usage:
  responder
  responder run [--build] <module>
  responder build
  responder --version

Options:
  -h --help     Show this screen.
  -v --version  Show version.

"""

import subprocess

import docopt

from .__version__ import __version__


def cli():
    """
    CLI interface handler of the Responder package.
    """
    args = docopt.docopt(__doc__, argv=None, version=__version__, options_first=False)

    module = args["<module>"]
    build = args["build"] or args["--build"]
    run = args["run"]

    if build:
        # S603, S607 are suppressed as we're using fixed arguments, not user input
        subprocess.check_call(["npm", "run", "build"])  # noqa: S603, S607

    if run:
        split_module = module.split(":")

        if len(split_module) > 1:
            module = split_module[0]
            prop = split_module[1]
        else:
            prop = "api"

        app = __import__(module)
        getattr(app, prop).run()
