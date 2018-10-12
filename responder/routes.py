from parse import parse, search


def memoize(f):
    memo = {}

    def helper(self, s):
        if s not in memo:
            memo[s] = f(self, s)
        return memo[s]

    return helper


class Route:
    def __init__(self, route, endpoint):
        self.route = route
        self.endpoint = endpoint

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

    def url(self, **params):
        return self.route.format(**params)

    # def is_graphql, is_wsgi
