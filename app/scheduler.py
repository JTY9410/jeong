from __future__ import annotations

from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from flask import Flask

from app.services.crawl_service import CrawlService


class SchedulerManager:
    def __init__(self, app: Flask):
        self.app = app
        self.scheduler = BackgroundScheduler(timezone=app.config["TIMEZONE"])

        # expose for API
        app.extensions["scheduler_manager"] = self

    def start(self) -> None:
        settings = self.app.extensions["settings"]
        hh, mm = settings.crawl_time.split(":")

        self.scheduler.add_job(
            self._run_public_and_intern,
            CronTrigger(hour=int(hh), minute=int(mm)),
            id="daily_crawl",
            replace_existing=True,
            max_instances=1,
        )

        self.scheduler.add_job(
            self._run_public_and_intern,
            IntervalTrigger(minutes=settings.internship_interval_minutes),
            id="internship_crawl",
            replace_existing=True,
            max_instances=1,
        )

        self.scheduler.start()

    def run_once(self, *, force_intern_keyword: bool = True) -> dict:
        return self._run_public_and_intern(force_intern_keyword=force_intern_keyword)

    def _run_public_and_intern(self, *, force_intern_keyword: bool = True) -> dict:
        SessionLocal = self.app.extensions["SessionLocal"]
        with self.app.app_context():
            with SessionLocal() as db:
                # prefer persisted default (settings page) for scheduled crawls
                try:
                    from app.services.settings_service import SettingsService

                    force_intern_keyword = SettingsService(db).get_force_intern_keyword()
                except Exception:
                    pass
                svc = CrawlService(db)
                return svc.run(force_intern_keyword=force_intern_keyword)

