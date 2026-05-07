from __future__ import annotations

import json
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crawling.keywords import DEFAULT_HUMANITIES_KEYWORDS
from app.models import AppSetting, SiteConfig


KEY_HUMANITIES_KEYWORDS = "humanities_keywords"
KEY_FORCE_INTERN_KEYWORD = "force_intern_keyword"


class SettingsService:
    def __init__(self, db: Session):
        self.db = db

    def get_keywords(self) -> list[str]:
        row = self.db.execute(select(AppSetting).where(AppSetting.key == KEY_HUMANITIES_KEYWORDS)).scalar_one_or_none()
        if not row:
            return DEFAULT_HUMANITIES_KEYWORDS
        try:
            val = json.loads(row.value)
            if isinstance(val, list) and all(isinstance(x, str) for x in val):
                return [x.strip() for x in val if x.strip()]
        except Exception:
            pass
        return DEFAULT_HUMANITIES_KEYWORDS

    def set_keywords(self, keywords: list[str]) -> None:
        keywords = [k.strip() for k in keywords if k and k.strip()]
        payload = json.dumps(keywords, ensure_ascii=False)
        row = self.db.execute(select(AppSetting).where(AppSetting.key == KEY_HUMANITIES_KEYWORDS)).scalar_one_or_none()
        if row:
            row.value = payload
        else:
            self.db.add(AppSetting(key=KEY_HUMANITIES_KEYWORDS, value=payload))
        self.db.commit()

    def get_force_intern_keyword(self) -> bool:
        """
        Default behavior for internship queries:
        - True: auto-append "인턴" when missing (broader coverage)
        - False: use keywords as-is (more precise)
        """
        row = self.db.execute(select(AppSetting).where(AppSetting.key == KEY_FORCE_INTERN_KEYWORD)).scalar_one_or_none()
        if not row:
            return True
        v = (row.value or "").strip().lower()
        if v in ("1", "true", "yes", "y", "on"):
            return True
        if v in ("0", "false", "no", "n", "off"):
            return False
        return True

    def set_force_intern_keyword(self, enabled: bool) -> None:
        payload = "true" if bool(enabled) else "false"
        row = self.db.execute(select(AppSetting).where(AppSetting.key == KEY_FORCE_INTERN_KEYWORD)).scalar_one_or_none()
        if row:
            row.value = payload
        else:
            self.db.add(AppSetting(key=KEY_FORCE_INTERN_KEYWORD, value=payload))
        self.db.commit()

    def ensure_sites(self, site_keys: list[str]) -> None:
        existing = {s.site_key for s in self.db.execute(select(SiteConfig)).scalars().all()}
        for k in site_keys:
            if k not in existing:
                self.db.add(SiteConfig(site_key=k, enabled=True))
        self.db.commit()

    def get_site_enabled_map(self) -> dict[str, bool]:
        rows = self.db.execute(select(SiteConfig)).scalars().all()
        return {r.site_key: bool(r.enabled) for r in rows}

    def set_site_enabled(self, site_key: str, enabled: bool) -> None:
        row = self.db.execute(select(SiteConfig).where(SiteConfig.site_key == site_key)).scalar_one_or_none()
        if not row:
            row = SiteConfig(site_key=site_key, enabled=enabled)
            self.db.add(row)
        else:
            row.enabled = enabled
        self.db.commit()

