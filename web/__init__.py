"""Browser-facing website (SSR via Jinja2, vanilla-JS for interactivity).

See :mod:`web.routes` for the routes and :mod:`web.templates` for the
HTML. Static assets live under :mod:`web.static`.
"""

from web.routes import mount, router, templates

__all__ = ["mount", "router", "templates"]