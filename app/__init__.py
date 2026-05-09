from __future__ import annotations

import os
from flask import Flask, jsonify, request
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

try:
    from flask_limiter.errors import RateLimitExceeded
except Exception:  # pragma: no cover
    RateLimitExceeded = None  # type: ignore[misc,assignment]

from app.config import Settings
from app.db import Base
from app.security import ensure_default_user
from app.scheduler import SchedulerManager


csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=["200 per day", "50 per hour"])


def _ensure_sqlite_fts(engine) -> None:
    """
    Create FTS5 virtual tables for fast search (SQLite only).
    This is best-effort; if FTS5 is unavailable, app still works with LIKE fallback.
    """
    driver = (engine.url.drivername or "").lower()
    if not driver.startswith("sqlite"):
        return

    with engine.begin() as conn:
        # job_postings
        conn.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS job_postings_fts
                USING fts5(title, company, site, location, description, url, content='job_postings', content_rowid='id');
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS job_postings_ai AFTER INSERT ON job_postings BEGIN
                  INSERT INTO job_postings_fts(rowid, title, company, site, location, description, url)
                  VALUES (new.id, new.title, new.company, new.site, new.location, new.description, new.url);
                END;
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS job_postings_ad AFTER DELETE ON job_postings BEGIN
                  INSERT INTO job_postings_fts(job_postings_fts, rowid, title, company, site, location, description, url)
                  VALUES('delete', old.id, old.title, old.company, old.site, old.location, old.description, old.url);
                END;
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS job_postings_au AFTER UPDATE ON job_postings BEGIN
                  INSERT INTO job_postings_fts(job_postings_fts, rowid, title, company, site, location, description, url)
                  VALUES('delete', old.id, old.title, old.company, old.site, old.location, old.description, old.url);
                  INSERT INTO job_postings_fts(rowid, title, company, site, location, description, url)
                  VALUES (new.id, new.title, new.company, new.site, new.location, new.description, new.url);
                END;
                """
            )
        )

        # internships
        conn.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS internships_fts
                USING fts5(title, company, site, location, description, url, content='internships', content_rowid='id');
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS internships_ai AFTER INSERT ON internships BEGIN
                  INSERT INTO internships_fts(rowid, title, company, site, location, description, url)
                  VALUES (new.id, new.title, new.company, new.site, new.location, new.description, new.url);
                END;
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS internships_ad AFTER DELETE ON internships BEGIN
                  INSERT INTO internships_fts(internships_fts, rowid, title, company, site, location, description, url)
                  VALUES('delete', old.id, old.title, old.company, old.site, old.location, old.description, old.url);
                END;
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS internships_au AFTER UPDATE ON internships BEGIN
                  INSERT INTO internships_fts(internships_fts, rowid, title, company, site, location, description, url)
                  VALUES('delete', old.id, old.title, old.company, old.site, old.location, old.description, old.url);
                  INSERT INTO internships_fts(rowid, title, company, site, location, description, url)
                  VALUES (new.id, new.title, new.company, new.site, new.location, new.description, new.url);
                END;
                """
            )
        )


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=False)

    settings = Settings.from_env()
    app.config.update(
        SECRET_KEY=settings.secret_key,
        ENV=settings.flask_env,
        DATABASE_URL=settings.database_url,
        TIMEZONE=settings.timezone,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )

    engine = create_engine(settings.database_url, future=True, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_fts(engine)

    app.extensions["engine"] = engine
    app.extensions["SessionLocal"] = SessionLocal
    app.extensions["settings"] = settings

    csrf.init_app(app)
    limiter.init_app(app)

    from app.web.routes import bp as web_bp

    app.register_blueprint(web_bp)

    def _is_api_path() -> bool:
        """프록시/리라이터 뒤에서도 `/api/` 경로만 JSON 처리."""
        path = request.path or ""
        return path == "/api" or path.startswith("/api/")

    @app.errorhandler(CSRFError)
    def _handle_csrf(err: CSRFError):
        if _is_api_path():
            detail = getattr(err, "description", None) or str(err)
            return jsonify({"ok": False, "error": "csrf_failed", "message": detail}), 400
        return (
            "세션이 만료되었거나 보안 토큰이 맞지 않습니다. 페이지를 새로고침한 뒤 다시 시도해 주세요.",
            400,
            {"Content-Type": "text/plain; charset=utf-8"},
        )

    if RateLimitExceeded is not None:

        @app.errorhandler(RateLimitExceeded)
        def _handle_rate_limit(err: RateLimitExceeded):
            if _is_api_path():
                detail = getattr(err, "description", None) or str(err)
                return jsonify({"ok": False, "error": "rate_limited", "message": detail}), 429
            return (
                "요청이 너무 잦습니다. 잠시 후 다시 시도해 주세요.",
                429,
                {"Content-Type": "text/plain; charset=utf-8"},
            )

    # seed default user (id/pw) if none exists
    with SessionLocal() as db:
        ensure_default_user(db, username=settings.default_username, password=settings.default_password)

    # start scheduler only when enabled (avoid double-run in Flask reloader)
    is_reloader_child = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    if settings.scheduler_enabled and (is_reloader_child or settings.flask_env != "development"):
        SchedulerManager(app).start()

    # Vercel 등 리버스 프록시 뒤: HTTPS·호스트 보정 없으면 세션 쿠키·CSRF 참조자 검증이 어긋날 수 있음
    if os.getenv("VERCEL") or os.getenv("VERCEL_ENV"):
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)
        app.config["SESSION_COOKIE_SECURE"] = True
        app.config["PREFERRED_URL_SCHEME"] = "https"

    return app

