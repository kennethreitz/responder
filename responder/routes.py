import re
import functools
from parse import parse


class Route:
    _param_pattern = re.compile(r"{([^{}]*)}")

    def __init__(
        self,
        route,
        endpoint,
        *,
        parse=False,
        websocket=False,
        before_request=False,
        mounted_app=False
    ):
        # if parse:

        self.route = route
        self.endpoint = endpoint
        self.uses_websocket = websocket
        self.before_request = before_request
        self.mounted_app = mounted_app

    def __repr__(self):
        return f"<Route {self.route!r}={self.endpoint!r}>"

    def __eq__(self, other):
        if hasattr(other, "route"):
            # Being compared to other routes.
            return self.route == other.route
        else:
            # Strings.
            return self.does_match(other)

    @property
    def endpoint_name(self):
        return self.endpoint.__name__

    @property
    def description(self):
        return self.endpoint.__doc__

    @property
    def has_parameters(self):
        return bool(self._param_pattern.search(self.route))

    @functools.lru_cache(maxsize=None)
    def does_match(self, s):
        if s == self.route:
            return True

        named = self.incoming_matches(s)
        return bool(len(named))

    @functools.lru_cache(maxsize=None)
    def incoming_matches(self, s):
        results = parse(self.route, s)
        return results.named if results else {}

    def url(self, **params):
        return self.route.format(**params)

    def _weight(self):
        params = set(self._param_pattern.findall(self.route))
        params_count = len(params)
        return params_count != 0, -params_count

    @property
    def is_class_based(self):
        return hasattr(self.endpoint, "__class__")

    @property
    def is_function(self):
        code = hasattr(self.endpoint, "__code__")
        kwdefaults = hasattr(self.endpoint, "__kwdefaults__")
        return all((callable(self.endpoint), code, kwdefaults))

    def __hash__(self):
        return (
            hash(self.route)
            ^ hash(self.endpoint)
            ^ hash(self.uses_websocket)
            ^ hash(self.before_request)
            ^ hash(self.mounted_app)
        )
