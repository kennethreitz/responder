"""Responder.

Usage:
  responder
  responder --version

Options:
  -h --help     Show this screen.
  -v --version     Show version.
  --speed=<kn>  Speed in knots [default: 10].
  --moored      Moored (anchored) mine.
  --drifting    Drifting mine.

"""


import docopt
from .__version__ import __version__


def cli():
    arguments = docopt.docopt(__doc__, argv=None, help=True,
                              version=__version__, options_first=False)
    print(arguments)
