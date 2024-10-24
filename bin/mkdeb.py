# ruff: noqa: S605, S607
"""
Build and publish a .deb package.
https://pypi.python.org/pypi/stdeb/0.8.5#quickstart-2-just-tell-me-the-fastest-way-to-make-a-deb
"""

import os
from shutil import rmtree

here = os.path.abspath(os.path.dirname(__file__))


def get_version():
    import responder

    return responder.__version__


def run():
    version = get_version()
    try:
        print("Removing previous builds")
        rmtree(os.path.join(here, "deb_dist"))
    except FileNotFoundError:
        pass
    print("Creating Debian package manifest")
    os.system(
        "python setup.py --command-packages=stdeb.command sdist_dsc "
        "-z artful --package3=pipenv --depends3=python3-virtualenv-clone"
    )
    print("Building .deb")
    os.chdir(f"deb_dist/pipenv-{version}")
    os.system("dpkg-buildpackage -rfakeroot -uc -us")


if __name__ == "__main__":
    run()
