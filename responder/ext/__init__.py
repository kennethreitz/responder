try:
    from .graphql import GraphQLView
except ModuleNotFoundError:
    # ignore graphql-server dependency errors as the extension is loaded even
    # it is not used. keep extension import for compat with precedent releases.
    pass
