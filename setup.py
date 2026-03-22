import os

from setuptools import find_packages, setup

here = os.path.abspath(os.path.dirname(__file__))

with open(os.path.join(here, "README.md"), encoding="utf-8") as f:
    long_description = "\n" + f.read()

about = {}
with open(os.path.join(here, "responder", "__version__.py")) as f:
    exec(f.read(), about)

required = [
    "a2wsgi",
    "chardet",
    "python-multipart",
    "servestatic",
    "starlette[full]>=0.40",
    "uvicorn[standard]",
]

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
    python_requires=">=3.9",
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
            "pyproject-fmt",
            "ruff",
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
        "graphql": ["graphene>=3", "graphql-core>=3.1"],
        "openapi": ["apispec>=1.0.0", "marshmallow"],
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
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: Implementation :: CPython",
        "Programming Language :: Python :: Implementation :: PyPy",
        "Topic :: Internet :: WWW/HTTP",
    ],
)
