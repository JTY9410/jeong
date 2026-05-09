from __future__ import annotations

import os
import secrets
from dataclasses import dataclass


def _coerce_postgres_url_for_psycopg3(database_url: str) -> str:
    """
    Neon/Heroku-style URLs use postgres:// or postgresql:// without a SQLAlchemy driver.
    SQLAlchemy then defaults to psycopg2, which this project does not install (only psycopg3).
    """
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgres://")
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
    return database_url


@dataclass(frozen=True)
class Settings:
    secret_key: str
    flask_env: str
    database_url: str
    timezone: str
    scheduler_enabled: bool
    crawl_time: str
    internship_interval_minutes: int
    default_username: str
    default_password: str

    @staticmethod
    def from_env() -> "Settings":
        # SQLite default (dev). For Postgres, set DATABASE_URL like:
        # postgresql+psycopg://user:pass@postgres:5432/recruitment
        #
        # NOTE: Serverless platforms (e.g. Vercel) have a read-only filesystem
        # except for /tmp. Use /tmp for the SQLite fallback to avoid crashes.
        is_vercel = bool(os.getenv("VERCEL") or os.getenv("VERCEL_ENV"))
        default_sqlite = "sqlite:////tmp/app.db" if is_vercel else "sqlite:///./data/app.db"
        database_url = os.getenv("DATABASE_URL", default_sqlite)
        if database_url.startswith(("postgres://", "postgresql://")):
            database_url = _coerce_postgres_url_for_psycopg3(database_url)

        secret_key = (os.getenv("SECRET_KEY") or "").strip()
        if is_vercel and not secret_key:
            raise RuntimeError(
                "Vercel에는 SECRET_KEY 환경 변수가 필수입니다. "
                "미설정이면 서버리스 인스턴스마다 임의 키가 달라져 세션·CSRF가 계속 실패합니다."
            )
        if not secret_key:
            secret_key = secrets.token_urlsafe(32)

        return Settings(
            secret_key=secret_key,
            flask_env=os.getenv("FLASK_ENV", "production"),
            database_url=database_url,
            timezone=os.getenv("TIMEZONE", "Asia/Seoul"),
            scheduler_enabled=os.getenv("SCHEDULER_ENABLED", "true").lower() == "true",
            crawl_time=os.getenv("CRAWL_TIME", "07:00"),
            internship_interval_minutes=int(os.getenv("INTERNSHIP_INTERVAL_MINUTES", "120")),
            default_username=os.getenv("DEFAULT_USERNAME", "jsy1004"),
            default_password=os.getenv("DEFAULT_PASSWORD", "jsy0701"),
        )

