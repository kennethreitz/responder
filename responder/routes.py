from parse import parse, search


def memoize(f):
    def helper(self, s):
        memoize_key = f'{f.__name__}:{s}'
        if memoize_key not in self._memo:
            self._memo[memoize_key] = f(self, s)
        return self._memo[memoize_key]

    return helper


class Route:
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
    def has_parameters(self):
        return all([("{" in self.route), ("}" in self.route)])

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
