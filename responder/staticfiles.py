import typing

from starlette.staticfiles import StaticFiles


class StaticFiles(StaticFiles):
    """I've created an issue to disccuss allowing multiple directories in starletter's `StaticFiles`.

    https://github.com/encode/starlette/issues/625

    I've also made a PR to add this method to starlette StaticFiles
    Once accepted we will remove this.

    https://github.com/encode/starlette/pull/626
    """

    def add_directory(self, directory: str) -> None:
        self.all_directories = [*self.all_directories, *self.get_directories(directory)]
