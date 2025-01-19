#!/usr/bin/env python
# -*- coding: utf-8 -*-
import codecs
import os
import sys
from shutil import rmtree

from setuptools import Command, find_packages, setup

here = os.path.abspath(os.path.dirname(__file__))

with codecs.open(os.path.join(here, "README.md"), encoding="utf-8") as f:
    long_description = "\n" + f.read()

about = {}

with open(os.path.join(here, "responder", "__version__.py")) as f:
    exec(f.read(), about)

if sys.argv[-1] == "publish":
    os.system("python setup.py sdist bdist_wheel upload")
    sys.exit()

required = [
    "apispec>=1.0.0b1",
    "chardet",
    "marshmallow",
    "requests",
    "requests-toolbelt",
    "rfc3986",
    # ServeStatic is the successor to WhiteNoise.
    # WhiteNoise is used for backward compatibility with Python <3.8.
    "servestatic; python_version>='3.8'",
    "starlette[full]",
    "uvicorn[standard]",
    "whitenoise; python_version<'3.8'",
]


# https://pypi.python.org/pypi/stdeb/0.8.5#quickstart-2-just-tell-me-the-fastest-way-to-make-a-deb
class DebCommand(Command):
    """Support for setup.py deb"""

    description = "Build and publish the .deb package."
    user_options = []

    @staticmethod
    def status(s):
        """Prints things in bold."""
        print("\033[1m{0}\033[0m".format(s))

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        try:
            self.status("Removing previous builds…")
            rmtree(os.path.join(here, "deb_dist"))
        except FileNotFoundError:
            pass
        self.status("Creating debian manifest…")
        os.system(
            "python setup.py --command-packages=stdeb.command sdist_dsc -z artful --package3=pipenv --depends3=python3-virtualenv-clone"
        )
        self.status("Building .deb…")
        os.chdir("deb_dist/pipenv-{0}".format(about["__version__"]))
        os.system("dpkg-buildpackage -rfakeroot -uc -us")


class UploadCommand(Command):
    """Support setup.py publish."""

    description = "Build and publish the package."
    user_options = []

    @staticmethod
    def status(s):
        """Prints things in bold."""
        print("\033[1m{0}\033[0m".format(s))

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        try:
            self.status("Removing previous builds…")
            rmtree(os.path.join(here, "dist"))
        except FileNotFoundError:
            pass
        self.status("Building Source distribution…")
        os.system("{0} setup.py sdist bdist_wheel".format(sys.executable))
        self.status("Uploading the package to PyPI via Twine…")
        os.system("twine upload dist/*")
        self.status("Pushing git tags…")
        os.system("git tag v{0}".format(about["__version__"]))
        os.system("git push --tags")
        sys.exit()


setup(
    name="responder",
    version=about["__version__"],
    description="A familiar HTTP Service Framework for Python.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Kenneth Reitz",
    author_email="me@kennethreitz.org",
    url="https://github.com/kennethreitz/responder",
    packages=find_packages(exclude=["tests"]),
    package_data={},
    entry_points={"console_scripts": ["responder=responder.ext.cli:cli"]},
    python_requires=">=3.7",
    setup_requires=[],
    install_requires=required,
    extras_require={
        "cli": [
            "docopt-ng",
            "pueblo[sfa]>=0.0.11",
        ],
        "cli-full": [
            "pueblo[sfa-full]>=0.0.11",
            "responder[cli]",
        ],
        "develop": [
            "poethepoet",
            "pyproject-fmt; python_version>='3.7'",
            "ruff; python_version>='3.7'",
            "validate-pyproject",
        ],
        "docs": [
            "alabaster<1.1",
            "myst-parser[linkify]",
            "sphinx>=5,<9",
            "sphinx-autobuild",
            "sphinx-copybutton",
            "sphinx-design-elements",
            "sphinxext.opengraph",
        ],
        "full": ["responder[cli-full,graphql,openapi]"],
        "graphql": ["graphene<3", "graphql-server-core>=1.2,<2"],
        "openapi": ["apispec>=1.0.0"],
        "release": ["build", "twine"],
        "test": [
            "flask",
            "mypy",
            "pytest",
            "pytest-cov",
            "pytest-mock",
            "pytest-rerunfailures",
        ],
    },
    include_package_data=True,
    license="Apache 2.0",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Web Environment",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: Implementation :: CPython",
        "Programming Language :: Python :: Implementation :: PyPy",
        "Topic :: Internet :: WWW/HTTP",
    ],
    cmdclass={"upload": UploadCommand, "deb": DebCommand},
)
