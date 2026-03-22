from starlette.staticfiles import StaticFiles as StarletteStaticFiles


class StaticFiles(StarletteStaticFiles):
    """Extension to Starlette's StaticFiles with support for multiple directories."""

    def add_directory(self, directory: str) -> None:
        self.all_directories = [*self.all_directories, *self.get_directories(directory)]
