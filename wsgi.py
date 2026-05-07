from __future__ import annotations

from app import create_app

# Standard WSGI entrypoint for most deployment platforms (Gunicorn, uWSGI, etc.)
app = create_app()

