# ruff: noqa: E501
GRAPHIQL = """
{% set GRAPHIQL_VERSION = '3.0.6' %}
{% set REACT_VERSION = '18.2.0' %}

<!DOCTYPE html>
<html>
  <head>
    <style>
      body {
        height: 100%;
        margin: 0;
        width: 100%;
        overflow: hidden;
      }
      #graphiql {
        height: 100vh;
      }
    </style>
    <link href="//cdn.jsdelivr.net/npm/graphiql@{{ GRAPHIQL_VERSION }}/graphiql.min.css" rel="stylesheet"/>
  </head>
  <body>
    <div id="graphiql">Loading...</div>
    <script crossorigin src="//cdn.jsdelivr.net/npm/react@{{ REACT_VERSION }}/umd/react.production.min.js"></script>
    <script crossorigin src="//cdn.jsdelivr.net/npm/react-dom@{{ REACT_VERSION }}/umd/react-dom.production.min.js"></script>
    <script src="//cdn.jsdelivr.net/npm/graphiql@{{ GRAPHIQL_VERSION }}/graphiql.min.js"></script>
    <script>
      const fetcher = GraphiQL.createFetcher({ url: {{ endpoint | tojson }} });
      const root = ReactDOM.createRoot(document.getElementById('graphiql'));
      root.render(React.createElement(GraphiQL, { fetcher: fetcher }));
    </script>
  </body>
</html>
""".strip()
