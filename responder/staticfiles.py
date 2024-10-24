from starlette.staticfiles import StaticFiles as StarletteStaticFiles


class StaticFiles(StarletteStaticFiles):
    """
    Extension to Starlette's `StaticFiles`.

    I've created an issue to discuss allowing multiple directories in
    Starlette's `StaticFiles`.

    https://github.com/encode/starlette/issues/625

    I've also made a PR to add this method to Starlette StaticFiles
    Once accepted we will remove this.

    https://github.com/encode/starlette/pull/626
    """

    def add_directory(self, directory: str) -> None:
        self.all_directories = [*self.all_directories, *self.get_directories(directory)]
