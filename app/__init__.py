from __future__ import annotations

import os
from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

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

    # seed default user (id/pw) if none exists
    with SessionLocal() as db:
        ensure_default_user(db, username=settings.default_username, password=settings.default_password)

    # start scheduler only when enabled (avoid double-run in Flask reloader)
    is_reloader_child = os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    if settings.scheduler_enabled and (is_reloader_child or settings.flask_env != "development"):
        SchedulerManager(app).start()

    return app

