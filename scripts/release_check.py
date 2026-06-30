from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def output(cmd: list[str], *, check: bool = True) -> str:
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def fail(message: str) -> None:
    print(f"release-check: {message}", file=sys.stderr)
    raise SystemExit(1)


def project_version() -> str:
    text = (ROOT / "responder" / "__version__.py").read_text()
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if match is None:
        fail("could not read responder.__version__")
    return match.group(1)


def check_clean_tree() -> None:
    status = output(["git", "status", "--porcelain"])
    if status:
        fail("working tree is not clean")


def check_changelog(version: str) -> None:
    text = (ROOT / "CHANGELOG.md").read_text()
    tag = f"v{version}"
    unreleased_link = (
        f"[Unreleased]: https://github.com/kennethreitz/responder/compare/{tag}..HEAD"
    )
    if f"## [{tag}]" not in text:
        fail(f"CHANGELOG.md has no section for {tag}")
    if unreleased_link not in text:
        fail("CHANGELOG.md Unreleased compare link does not start at current version")
    if f"[{tag}]: https://github.com/kennethreitz/responder/compare/" not in text:
        fail(f"CHANGELOG.md has no compare link for {tag}")


def check_tag(version: str, *, require_absent: bool) -> None:
    tag = f"v{version}"
    ref = f"refs/tags/{tag}^{{}}"
    result = subprocess.run(
        ["git", "rev-parse", "--verify", ref],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        return
    if require_absent:
        fail(f"tag {tag} already exists")
    head = output(["git", "rev-parse", "HEAD"])
    if result.stdout.strip() != head:
        fail(f"tag {tag} exists but does not point at HEAD")


def check_wheel_contents(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())

    blocked = sorted(
        name for name in names if name.startswith(("docs/", "examples/", "tests/"))
    )
    if blocked:
        sample = ", ".join(blocked[:5])
        fail(f"wheel includes non-package files: {sample}")

    required = {
        "responder/py.typed",
        "responder/ext/openapi/docs/swagger_ui.html",
    }
    missing = sorted(required - names)
    if missing:
        fail("wheel is missing required package data: " + ", ".join(missing))


def build_and_check(*, twine_check: bool) -> None:
    version = project_version()
    with tempfile.TemporaryDirectory(prefix="responder-dist-") as tmp:
        dist = Path(tmp)
        run(["uv", "build", "--out-dir", str(dist)])
        artifacts = sorted(dist.glob(f"responder-{version}*"))
        wheels = [path for path in artifacts if path.suffix == ".whl"]
        sdists = [path for path in artifacts if path.name.endswith(".tar.gz")]
        if len(wheels) != 1 or len(sdists) != 1:
            fail(f"expected one wheel and one sdist for {version}")
        check_wheel_contents(wheels[0])
        if twine_check:
            run(["uvx", "twine", "check", *(str(path) for path in artifacts)])


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Responder release checks.")
    parser.add_argument("--skip-clean", action="store_true")
    parser.add_argument("--skip-tag-check", action="store_true")
    parser.add_argument("--require-tag-absent", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-lint", action="store_true")
    parser.add_argument("--skip-types", action="store_true")
    parser.add_argument("--skip-docs", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-twine-check", action="store_true")
    args = parser.parse_args()

    version = project_version()
    print(f"release-check: responder {version}")
    check_changelog(version)
    if not args.skip_clean:
        check_clean_tree()
    if not args.skip_tag_check:
        check_tag(version, require_absent=args.require_tag_absent)
    if not args.skip_lint:
        run(["uv", "run", "--extra", "develop", "ruff", "check", "."])
    if not args.skip_types:
        run(["uv", "run", "--extra", "test", "mypy"])
    if not args.skip_tests:
        run(["uv", "run", "--extra", "test", "pytest", "-q"])
    if not args.skip_docs:
        with tempfile.TemporaryDirectory(prefix="responder-docs-") as tmp:
            run(
                [
                    "uv",
                    "run",
                    "--extra",
                    "docs",
                    "sphinx-build",
                    "-W",
                    "-b",
                    "html",
                    "docs/source",
                    tmp,
                ]
            )
    if not args.skip_build:
        build_and_check(twine_check=not args.skip_twine_check)


if __name__ == "__main__":
    main()
