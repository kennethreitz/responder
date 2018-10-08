class BaseAPI:
    def __init__(self, *, yaml_allowed):
        self.yaml_allowed = yaml_allowed
        self.routes = {}

    @property
    def should_yaml(self):
        return self.yaml_allowed


class API(BaseAPI):
    def __init__(self, *, yaml_allowed=True):
        super().__init__(yaml_allowed=yaml_allowed)

    def add_route(self, route, view, *, check_existing=True):
        if check_existing:
            assert route not in self.routes

        self.routes[route] = view

    def route(self, route, **options):
        def decorator(f):
            self.add_route(route, f)
            return f

        return decorator
