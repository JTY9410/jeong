from __future__ import annotations

import os

from app import create_app


# Vercel Python runtime entrypoint.
# Expose WSGI `app` at module level.
#
# Note: Playwright-based crawling is generally not suitable for Vercel serverless.
# Keep scheduler off by default in this environment.
os.environ.setdefault("SCHEDULER_ENABLED", "false")

app = create_app()

