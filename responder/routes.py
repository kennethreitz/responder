import re
from parse import parse


def memoize(f):
    def helper(self, s):
        memoize_key = f"{f.__name__}:{s}"
        if memoize_key not in self._memo:
            self._memo[memoize_key] = f(self, s)
        return self._memo[memoize_key]

    return helper


class Route:
    _param_pattern = re.compile(r"{([^{}]*)}")

    def __init__(self, route, endpoint):
        self.route = route
        self.endpoint = endpoint
        self._memo = {}

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
    def description(self):
        return self.endpoint.__doc__

    @property
    def has_parameters(self):
        return bool(self._param_pattern.search(self.route))

    @memoize
    def does_match(self, s):
        if s == self.route:
            return True

        named = self.incoming_matches(s)
        return bool(len(named))

    @memoize
    def incoming_matches(self, s):
        results = parse(self.route, s)
        return results.named if results else {}

    def url(self, testing=False, **params):
        url = self.route.format(**params)
        if testing:
            url = f"http://;{url}"

        return url

    def _weight(self):
        params = set(self._param_pattern.findall(self.route))
        params_count = -len(params) or 0
        return params_count != 0, params_count

    @property
    def is_graphql(self):
        return hasattr(self.endpoint, "get_graphql_type")
