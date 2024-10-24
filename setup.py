#!/usr/bin/env python
# -*- coding: utf-8 -*-
import codecs
import os

from setuptools import find_packages, setup
from versioningit import get_cmdclasses

here = os.path.abspath(os.path.dirname(__file__))

with codecs.open(os.path.join(here, "README.md"), encoding="utf-8") as f:
    long_description = "\n" + f.read()

required = [
    "aiofiles",
    "apispec>=1.0.0b1",
    "chardet",
    "docopt-ng",
    "marshmallow",
    "requests",
    "requests-toolbelt",
    "rfc3986",
    "starlette[full]",
    "uvicorn[standard]",
    "whitenoise",
]

setup(
    name="responder",
    description="A familiar HTTP Service Framework for Python.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Kenneth Reitz",
    author_email="me@kennethreitz.org",
    url="https://github.com/kennethreitz/responder",
    packages=find_packages(exclude=["tests"]),
    package_data={},
    python_requires=">=3.10",
    setup_requires=[],
    install_requires=required,
    extras_require={
        "develop": ["poethepoet", "pyproject-fmt", "ruff", "validate-pyproject"],
        "graphql": ["graphene"],
        "release": ["build", "twine"],
        "test": ["pytest", "pytest-cov", "pytest-mock", "flask"],
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
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: Implementation :: CPython",
        "Programming Language :: Python :: Implementation :: PyPy",
        "Topic :: Internet :: WWW/HTTP",
    ],
    cmdclass=get_cmdclasses(),
)
