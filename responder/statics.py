API_THEMES = ["elements", "rapidoc", "redoc", "swagger_ui"]
DEFAULT_ENCODING = "utf-8"
DEFAULT_OPENAPI_THEME = "swagger_ui"
DEFAULT_SESSION_COOKIE = "Responder-Session"
DEFAULT_SECRET_KEY = "NOTASECRET"  # noqa: S105

DEFAULT_CORS_PARAMS = {
    "allow_origins": (),
    "allow_methods": ("GET",),
    "allow_headers": (),
    "allow_credentials": False,
    "allow_origin_regex": None,
    "expose_headers": (),
    "max_age": 600,
}
