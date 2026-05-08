from __future__ import annotations

import os

# Vercel이 루트 wsgi.py를 진입점으로 잡을 때도 스케줄러는 끕니다 (api/index.py와 동일).
if os.getenv("VERCEL") or os.getenv("VERCEL_ENV"):
    os.environ["SCHEDULER_ENABLED"] = "false"
else:
    os.environ.setdefault("SCHEDULER_ENABLED", "false")

from app import create_app

# Standard WSGI entrypoint for most deployment platforms (Gunicorn, uWSGI, etc.)
app = create_app()

