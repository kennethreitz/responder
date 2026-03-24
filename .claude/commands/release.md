Release a new version of responder to PyPI and GitHub.

Usage: /release <version> (e.g. /release 3.6.0)

If no version is provided, ask the user what version to release.

## Steps

1. **Verify clean state**: Run `git status` and ensure the working tree is clean. If not, stop and ask the user.

2. **Run tests**: Run `uv run pytest -x --no-header -q`. If any fail, stop and report.

3. **Bump version**: Update `responder/__version__.py` to the new version.

4. **Update changelog**:
   - Run `git log --oneline $(git describe --tags --abbrev=0)..HEAD` to get commits since last release.
   - Add a new section in `CHANGELOG.md` under `## [Unreleased]` with the date, categorized into Added/Changed/Fixed/Removed.
   - Update the compare links at the bottom of the file.

5. **Lock deps**: Run `uv lock`.

6. **Commit**: Stage `responder/__version__.py`, `CHANGELOG.md`, and `uv.lock`. Commit with message `Bump version to X.Y.Z and update changelog`.

7. **Push and tag**:
   ```
   git push
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

8. **GitHub release**: Create a release with `gh release create` including highlights and a link to the full changelog.

9. **Build and publish**:
   ```
   uv build
   uvx twine upload dist/responder-X.Y.Z*
   ```
   Note: This requires a PyPI token. If twine fails due to auth, tell the user to set `TWINE_USERNAME=__token__` and `TWINE_PASSWORD` and re-run, or run `! uvx twine upload dist/responder-X.Y.Z*` interactively.

10. **Update GitHub release**: Edit the release to add a link to the PyPI page: `https://pypi.org/project/responder/X.Y.Z/`

11. **Report**: Print a summary with links to the GitHub release and PyPI page.
