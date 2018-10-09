import graphene


class GraphQLSchema:
    def __init__(self, **kwargs):
        self.schema = graphene.Schema(**kwargs)
