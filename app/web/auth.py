from __future__ import annotations

from functools import wraps
from flask import session, redirect, url_for, request, jsonify


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("web.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


def api_login_required(view):
    """
    API 전용 로그인 가드.
    브라우저 fetch(XHR)에서 HTML 리다이렉트(/login)를 받으면 JSON 파싱이 깨지므로,
    인증이 없으면 JSON으로 401을 반환합니다.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return view(*args, **kwargs)

    return wrapped

