"""Responder.

Usage:
  responder
  responder run [--build] [--debug] <module>
  responder build
  responder --version

Options:
  -h --help     Show this screen.
  -v --version  Show version.

"""

import os

import docopt
from .__version__ import __version__


def cli():
    args = docopt.docopt(
        __doc__, argv=None, help=True, version=__version__, options_first=False
    )

    module = args["<module>"]
    build = args["build"] or args["--build"]
    run = args["run"]

    if build:
        os.system("npm run build")

    if run:
        split_module = module.split(":")

        if len(split_module) > 1:
            module = split_module[0]
            prop = split_module[1]
        else:
            prop = "api"

        app = __import__(module)
        getattr(app, prop).run()