from __future__ import annotations

import os

from app import create_app


# Vercel Python runtime entrypoint.
# Expose WSGI `app` at module level.
#
# Note: Playwright-based crawling is generally not suitable for Vercel serverless.
# Never start APScheduler on Vercel (even if the dashboard sets SCHEDULER_ENABLED=true).
if os.getenv("VERCEL") or os.getenv("VERCEL_ENV"):
    os.environ["SCHEDULER_ENABLED"] = "false"
else:
    os.environ.setdefault("SCHEDULER_ENABLED", "false")

app = create_app()

