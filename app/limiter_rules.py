from __future__ import annotations

from flask import request
from app import limiter


def login_limiter(func):
    # brute-force protection
    return limiter.limit("10 per minute", key_func=lambda: request.remote_addr)(func)


def api_limiter(func):
    return limiter.limit("60 per minute", key_func=lambda: request.remote_addr)(func)

