from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_check import ROOT, check_changelog, check_tag, fail, output, project_version


def command_text(cmd: list[str]) -> str:
    return shlex.join(cmd)


def run(cmd: list[str], *, execute: bool) -> None:
    print("+ " + command_text(cmd))
    if execute:
        subprocess.run(cmd, cwd=ROOT, check=True)


def current_branch() -> str:
    branch = output(["git", "branch", "--show-current"])
    if not branch:
        fail("could not determine the current git branch")
    return branch


def changelog_notes(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text()
    heading = f"## [v{version}]"
    start = text.find(heading)
    if start == -1:
        fail(f"CHANGELOG.md has no section for v{version}")
    next_start = text.find("\n## [", start + len(heading))
    section = text[start: next_start if next_start != -1 else len(text)]
    return section.strip()


def clean_dist(*, execute: bool) -> None:
    dist = ROOT / "dist"
    print("+ rm -rf dist")
    if execute and dist.exists():
        shutil.rmtree(dist)


def upload_dist(version: str, *, pypirc: Path, execute: bool) -> None:
    if execute:
        artifacts = sorted((ROOT / "dist").glob(f"responder-{version}*"))
        if not artifacts:
            fail(f"dist/ has no artifacts for responder {version}")
        cmd = [
            "uvx",
            "twine",
            "upload",
            "--config-file",
            str(pypirc),
            *(str(path) for path in artifacts),
        ]
    else:
        cmd = [
            "uvx",
            "twine",
            "upload",
            "--config-file",
            str(pypirc),
            f"dist/responder-{version}*",
        ]
    run(cmd, execute=execute)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cut a Responder release. Defaults to dry-run."
    )
    parser.add_argument("version", nargs="?", help="Release version, without the v.")
    parser.add_argument("--execute", action="store_true", help="Run the commands.")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--pypirc", default="~/.pypirc")
    parser.add_argument("--skip-checks", action="store_true")
    parser.add_argument("--skip-tag-check", action="store_true")
    parser.add_argument("--skip-push", action="store_true")
    parser.add_argument("--skip-gh-release", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-upload", action="store_true")
    args = parser.parse_args()

    version = args.version or project_version()
    current_version = project_version()
    if version != current_version:
        fail(
            f"requested {version}, but responder.__version__ is {current_version}"
        )

    tag = f"v{version}"
    branch = current_branch()
    pypirc = Path(args.pypirc).expanduser()

    check_changelog(version)
    if not args.skip_tag_check:
        check_tag(version, require_absent=True)

    if not args.execute:
        print("release: dry-run; pass --execute to run these commands")

    if not args.skip_checks:
        run(
            [
                sys.executable,
                "scripts/release_check.py",
                "--require-tag-absent",
            ],
            execute=args.execute,
        )

    run(["git", "tag", tag], execute=args.execute)

    if not args.skip_push:
        run(["git", "push", args.remote, branch], execute=args.execute)
        run(["git", "push", args.remote, tag], execute=args.execute)

    if not args.skip_gh_release:
        notes = changelog_notes(version)
        if args.execute:
            run(
                ["gh", "release", "create", tag, "--title", tag, "--notes", notes],
                execute=True,
            )
        else:
            print(
                "+ gh release create "
                f"{shlex.quote(tag)} --title {shlex.quote(tag)} "
                "--notes '<CHANGELOG section>'"
            )

    if not args.skip_build:
        clean_dist(execute=args.execute)
        run(["uv", "build"], execute=args.execute)

    if not args.skip_upload:
        upload_dist(version, pypirc=pypirc, execute=args.execute)


if __name__ == "__main__":
    main()
