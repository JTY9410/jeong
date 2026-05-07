from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import JobPosting, Internship, Notification
from app.services.settings_service import SettingsService


class CrawlService:
    def __init__(self, db: Session):
        self.db = db

    def run(self, *, force_intern_keyword: bool = True) -> dict:
        # Import scrapers lazily so serverless deployments (e.g. Vercel) can run
        # the web UI without pulling heavy crawling dependencies at import time.
        from app.crawling.scrapers import scrape_all

        settings = SettingsService(self.db)
        keywords = settings.get_keywords()
        enabled = settings.get_site_enabled_map()
        jobs, interns = asyncio.run(
            scrape_all(
                keywords=keywords,
                enabled=enabled,
                force_intern_keyword=force_intern_keyword,
            )
        )

        new_jobs = 0
        new_interns = 0
        new_notifications = 0

        for j in jobs:
            jp = JobPosting(
                site=j.site,
                title=j.title,
                company=j.company,
                deadline=j.deadline,
                url=j.url,
                job_type=j.job_type,
                location=j.location,
                posted_date=j.posted_date,
                description=j.description,
                crawled_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
            self.db.add(jp)
            try:
                self.db.commit()
            except IntegrityError:
                self.db.rollback()
                continue
            new_jobs += 1
            n = Notification(job_posting_id=jp.id)
            self.db.add(n)
            jp.is_notified = True
            self.db.commit()
            new_notifications += 1

        for it in interns:
            iv = Internship(
                site=it.site,
                title=it.title,
                company=it.company,
                start_date=it.start_date,
                end_date=it.end_date,
                application_deadline=it.application_deadline,
                url=it.url,
                location=it.location,
                description=it.description,
                crawled_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
            self.db.add(iv)
            try:
                self.db.commit()
            except IntegrityError:
                self.db.rollback()
                continue
            new_interns += 1
            n = Notification(internship_id=iv.id)
            self.db.add(n)
            iv.is_notified = True
            self.db.commit()
            new_notifications += 1

        return {
            "new_jobs": new_jobs,
            "new_internships": new_interns,
            "new_notifications": new_notifications,
        }

