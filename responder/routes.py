import re
import functools
import inspect
from parse import parse, with_pattern


def _make_convertor(type, pattern):
    @with_pattern(pattern)
    def inner(value):
        return type(value)

    return inner


_convertors = {
    "int": _make_convertor(int, r"\d+"),
    "str": _make_convertor(str, r"[^/]+"),
    "float": _make_convertor(float, r"\d+(.\d+)?"),
}


class Route:
    _param_pattern = re.compile(r"{([^{}]*)}")

    def __init__(self, route, endpoint, *, websocket=False, before_request=False):
        self.route = route
        self.endpoint = endpoint
        self.uses_websocket = websocket
        self.before_request = before_request

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
        results = parse(self.route, s, _convertors)
        return results.named if results else {}

    def url(self, **params):
        return self.route.format(**params)

    def _weight(self):
        params = set(self._param_pattern.findall(self.route))
        params_count = len(params)
        w = len(self.route.rsplit("}", 1)[-1].strip("/"))
        return params_count != 0, w == 0, -params_count

    @property
    def is_class_based(self):
        return inspect.isclass(self.endpoint)

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
        )
