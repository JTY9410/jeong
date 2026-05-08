from __future__ import annotations

from datetime import datetime, timedelta
import os
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, session, jsonify
from sqlalchemy import select, func, desc, text

from app.limiter_rules import login_limiter, api_limiter
from app.models import User, JobPosting, Internship, Notification
from app.security import verify_password
from app.web.forms import LoginForm
from app.web.auth import login_required, api_login_required
from app.services.settings_service import SettingsService
from app.crawling.sites import SITES
from app.services.crawl_service import CrawlService


bp = Blueprint("web", __name__)


def _db():
    return current_app.extensions["SessionLocal"]()


@bp.get("/")
def root():
    if session.get("user_id"):
        return redirect(url_for("web.dashboard"))
    return redirect(url_for("web.login"))


@bp.route("/login", methods=["GET", "POST"])
@login_limiter
def login():
    form = LoginForm()
    if form.validate_on_submit():
        with _db() as db:
            user = db.execute(select(User).where(User.username == form.username.data)).scalar_one_or_none()
            if not user or not verify_password(form.password.data, user.password_hash):
                flash("아이디 또는 비밀번호가 올바르지 않습니다.", "danger")
                return render_template("login.html", form=form)

            session["user_id"] = user.id
            session["username"] = user.username
            flash("로그인되었습니다.", "success")
            nxt = request.args.get("next") or url_for("web.dashboard")
            return redirect(nxt)

    return render_template("login.html", form=form)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("web.login"))


@bp.get("/dashboard")
@login_required
def dashboard():
    now = datetime.utcnow()
    since = now - timedelta(days=14)
    with _db() as db:
        jobs_14d_rows = db.execute(
            select(func.date(JobPosting.created_at), func.count())
            .where(JobPosting.created_at >= since)
            .group_by(func.date(JobPosting.created_at))
            .order_by(func.date(JobPosting.created_at))
        ).all()

        interns_14d_rows = db.execute(
            select(func.date(Internship.created_at), func.count())
            .where(Internship.created_at >= since)
            .group_by(func.date(Internship.created_at))
            .order_by(func.date(Internship.created_at))
        ).all()

        # Ensure JSON-serializable rows for Jinja `tojson`
        jobs_14d = [[(str(d) if d is not None else ""), int(c)] for (d, c) in jobs_14d_rows]
        interns_14d = [[(str(d) if d is not None else ""), int(c)] for (d, c) in interns_14d_rows]

        unread_count = db.execute(
            select(func.count(Notification.id)).where(Notification.read_at.is_(None))
        ).scalar_one()

        latest_jobs = db.execute(select(JobPosting).order_by(desc(JobPosting.created_at)).limit(10)).scalars().all()
        latest_interns = db.execute(select(Internship).order_by(desc(Internship.created_at)).limit(10)).scalars().all()

    return render_template(
        "dashboard.html",
        jobs_14d=jobs_14d,
        interns_14d=interns_14d,
        unread_count=unread_count,
        latest_jobs=latest_jobs,
        latest_interns=latest_interns,
    )


@bp.get("/jobs")
@login_required
def jobs():
    q = (request.args.get("q") or "").strip()
    site = (request.args.get("site") or "").strip()
    with _db() as db:
        stmt = select(JobPosting).order_by(desc(JobPosting.created_at))
        if q:
            like = f"%{q}%"
            stmt = stmt.where((JobPosting.title.like(like)) | (JobPosting.company.like(like)))
        if site:
            stmt = stmt.where(JobPosting.site == site)
        items = db.execute(stmt.limit(300)).scalars().all()
    return render_template("jobs.html", items=items, q=q, site=site)


@bp.get("/internships")
@login_required
def internships():
    q = (request.args.get("q") or "").strip()
    site = (request.args.get("site") or "").strip()
    with _db() as db:
        stmt = select(Internship).order_by(desc(Internship.created_at))
        if q:
            like = f"%{q}%"
            stmt = stmt.where((Internship.title.like(like)) | (Internship.company.like(like)))
        if site:
            stmt = stmt.where(Internship.site == site)
        items = db.execute(stmt.limit(300)).scalars().all()
    return render_template("internships.html", items=items, q=q, site=site)


@bp.get("/search")
@login_required
def search():
    q = (request.args.get("q") or "").strip()
    typ = (request.args.get("type") or "").strip()  # "", "job", "internship"

    results: list[dict] = []
    if not q:
        return render_template("search.html", q=q, type=typ, results=results)

    engine = current_app.extensions["engine"]
    driver = (engine.url.drivername or "").lower()

    with _db() as db:
        # Prefer SQLite FTS5 when available, fallback to LIKE search.
        if driver.startswith("sqlite"):
            # FTS query: allow simple terms; keep it conservative to avoid syntax errors.
            fts_q = q.replace('"', " ").strip()

            def _fts_jobs():
                sql = text(
                    """
                    SELECT
                      'job' AS kind,
                      j.site AS site,
                      j.title AS title,
                      j.company AS company,
                      j.url AS url,
                      j.location AS location,
                      COALESCE(j.deadline, '') AS deadline,
                      COALESCE(j.description, '') AS description,
                      j.created_at AS created_at,
                      bm25(job_postings_fts) AS rank
                    FROM job_postings_fts
                    JOIN job_postings j ON j.id = job_postings_fts.rowid
                    WHERE job_postings_fts MATCH :q
                    ORDER BY rank
                    LIMIT 120
                    """
                )
                return db.execute(sql, {"q": fts_q}).mappings().all()

            def _fts_interns():
                sql = text(
                    """
                    SELECT
                      'internship' AS kind,
                      i.site AS site,
                      i.title AS title,
                      i.company AS company,
                      i.url AS url,
                      i.location AS location,
                      COALESCE(i.application_deadline, '') AS deadline,
                      COALESCE(i.description, '') AS description,
                      i.created_at AS created_at,
                      bm25(internships_fts) AS rank
                    FROM internships_fts
                    JOIN internships i ON i.id = internships_fts.rowid
                    WHERE internships_fts MATCH :q
                    ORDER BY rank
                    LIMIT 120
                    """
                )
                return db.execute(sql, {"q": fts_q}).mappings().all()

            try:
                if typ in ("", "job"):
                    results += list(_fts_jobs())
                if typ in ("", "internship"):
                    results += list(_fts_interns())
                results.sort(key=lambda x: float(x.get("rank") or 0.0))
                results = results[:240]
            except Exception:
                like = f"%{q}%"
                if typ in ("", "job"):
                    rows = db.execute(
                        select(JobPosting)
                        .where(
                            (JobPosting.title.like(like))
                            | (JobPosting.company.like(like))
                            | (JobPosting.description.like(like))
                            | (JobPosting.location.like(like))
                        )
                        .order_by(desc(JobPosting.created_at))
                        .limit(120)
                    ).scalars()
                    results += [
                        {
                            "kind": "job",
                            "site": r.site,
                            "title": r.title,
                            "company": r.company,
                            "url": r.url,
                            "location": r.location,
                            "deadline": r.deadline,
                            "description": r.description,
                            "created_at": (r.created_at.isoformat() if r.created_at else None),
                        }
                        for r in rows
                    ]
                if typ in ("", "internship"):
                    rows = db.execute(
                        select(Internship)
                        .where(
                            (Internship.title.like(like))
                            | (Internship.company.like(like))
                            | (Internship.description.like(like))
                            | (Internship.location.like(like))
                        )
                        .order_by(desc(Internship.created_at))
                        .limit(120)
                    ).scalars()
                    results += [
                        {
                            "kind": "internship",
                            "site": r.site,
                            "title": r.title,
                            "company": r.company,
                            "url": r.url,
                            "location": r.location,
                            "deadline": r.application_deadline,
                            "description": r.description,
                            "created_at": (r.created_at.isoformat() if r.created_at else None),
                        }
                        for r in rows
                    ]
        else:
            # Generic fallback (e.g., Postgres): LIKE-based, broaden fields.
            like = f"%{q}%"
            if typ in ("", "job"):
                rows = db.execute(
                    select(JobPosting)
                    .where(
                        (JobPosting.title.like(like))
                        | (JobPosting.company.like(like))
                        | (JobPosting.description.like(like))
                        | (JobPosting.location.like(like))
                    )
                    .order_by(desc(JobPosting.created_at))
                    .limit(120)
                ).scalars()
                results += [
                    {
                        "kind": "job",
                        "site": r.site,
                        "title": r.title,
                        "company": r.company,
                        "url": r.url,
                        "location": r.location,
                        "deadline": r.deadline,
                        "description": r.description,
                        "created_at": (r.created_at.isoformat() if r.created_at else None),
                    }
                    for r in rows
                ]
            if typ in ("", "internship"):
                rows = db.execute(
                    select(Internship)
                    .where(
                        (Internship.title.like(like))
                        | (Internship.company.like(like))
                        | (Internship.description.like(like))
                        | (Internship.location.like(like))
                    )
                    .order_by(desc(Internship.created_at))
                    .limit(120)
                ).scalars()
                results += [
                    {
                        "kind": "internship",
                        "site": r.site,
                        "title": r.title,
                        "company": r.company,
                        "url": r.url,
                        "location": r.location,
                        "deadline": r.application_deadline,
                        "description": r.description,
                        "created_at": (r.created_at.isoformat() if r.created_at else None),
                    }
                    for r in rows
                ]

    # Make created_at a friendly string for template
    for r in results:
        if isinstance(r.get("created_at"), datetime):
            r["created_at"] = r["created_at"].strftime("%Y-%m-%d")
        elif isinstance(r.get("created_at"), str) and "T" in r["created_at"]:
            r["created_at"] = r["created_at"].split("T", 1)[0]

    return render_template("search.html", q=q, type=typ, results=results)


@bp.get("/api/notifications/unread")
@api_login_required
@api_limiter
def api_unread_notifications():
    with _db() as db:
        rows = db.execute(
            select(Notification)
            .where(Notification.read_at.is_(None))
            .order_by(desc(Notification.created_at))
            .limit(20)
        ).scalars().all()

        payload = []
        for n in rows:
            if n.job_posting_id:
                job = n.job_posting
                payload.append(
                    {
                        "id": n.id,
                        "type": "job",
                        "created_at": n.created_at.isoformat(),
                        "title": job.title,
                        "company": job.company,
                        "url": job.url,
                    }
                )
            elif n.internship_id:
                it = n.internship
                payload.append(
                    {
                        "id": n.id,
                        "type": "internship",
                        "created_at": n.created_at.isoformat(),
                        "title": it.title,
                        "company": it.company,
                        "url": it.url,
                    }
                )

    return jsonify({"items": payload})


@bp.post("/api/notifications/mark-read")
@api_login_required
@api_limiter
def api_mark_read():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    if not isinstance(ids, list):
        return jsonify({"ok": False, "error": "ids must be list"}), 400

    with _db() as db:
        now = datetime.utcnow()
        ns = db.execute(select(Notification).where(Notification.id.in_(ids))).scalars().all()
        for n in ns:
            if n.read_at is None:
                n.read_at = now
        db.commit()

    return jsonify({"ok": True})


@bp.post("/api/crawl/run")
@api_login_required
@api_limiter
def api_run_crawl():
    def _parse_bool(s: str | None) -> bool | None:
        if s is None:
            return None
        v = s.strip().lower()
        if v in ("1", "true", "yes", "y", "on"):
            return True
        if v in ("0", "false", "no", "n", "off"):
            return False
        return None

    data = request.get_json(silent=True) or {}
    if "force_intern_keyword" in data:
        force_intern_keyword = bool(data.get("force_intern_keyword"))
    else:
        qv = _parse_bool(request.args.get("force_intern_keyword"))
        if qv is not None:
            force_intern_keyword = qv
        else:
            with _db() as db:
                force_intern_keyword = SettingsService(db).get_force_intern_keyword()

    # Vercel -> proxy the crawl request to an external crawler server (EC2).
    if os.getenv("VERCEL") or os.getenv("VERCEL_ENV"):
        crawler_url = (os.getenv("CRAWLER_PROXY_URL") or "").rstrip("/")
        secret = os.getenv("CRAWLER_SHARED_SECRET") or ""
        if not crawler_url or not secret:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "Crawler proxy is not configured. Set CRAWLER_PROXY_URL and CRAWLER_SHARED_SECRET.",
                    }
                ),
                500,
            )

        payload = json.dumps({"force_intern_keyword": bool(force_intern_keyword)}).encode("utf-8")
        req = Request(
            f"{crawler_url}/internal/crawl/run",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-CRAWL-SECRET": secret,
            },
        )
        try:
            with urlopen(req, timeout=170) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
            except Exception:
                return jsonify({"ok": False, "error": "Invalid response from crawler", "raw": body}), 502
            return jsonify(data), 200
        except HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            return jsonify({"ok": False, "error": "Crawler error", "status": e.code, "raw": raw}), 502
        except URLError as e:
            return jsonify({"ok": False, "error": f"Crawler unreachable: {e}"}), 502
        except Exception as e:
            return jsonify({"ok": False, "error": f"Crawler request failed: {e}"}), 502

    # Always allow manual crawl even if scheduler is disabled
    mgr = current_app.extensions.get("scheduler_manager")
    if mgr:
        result = mgr.run_once(force_intern_keyword=force_intern_keyword)
        return jsonify({"ok": True, "result": result})

    # fallback: run directly
    SessionLocal = current_app.extensions["SessionLocal"]
    with SessionLocal() as db:
        result = CrawlService(db).run(force_intern_keyword=force_intern_keyword)
    return jsonify({"ok": True, "result": result})


@bp.post("/internal/crawl/run")
def internal_run_crawl():
    """
    Internal endpoint intended for the crawler server (EC2).
    Protected by a shared secret header.
    """
    secret = os.getenv("CRAWLER_SHARED_SECRET") or ""
    if not secret or request.headers.get("X-CRAWL-SECRET") != secret:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    force_intern_keyword = bool(data.get("force_intern_keyword", True))

    mgr = current_app.extensions.get("scheduler_manager")
    if mgr:
        result = mgr.run_once(force_intern_keyword=force_intern_keyword)
        return jsonify({"ok": True, "result": result})

    SessionLocal = current_app.extensions["SessionLocal"]
    with SessionLocal() as db:
        result = CrawlService(db).run(force_intern_keyword=force_intern_keyword)
    return jsonify({"ok": True, "result": result})


@bp.get("/crawl")
@login_required
def crawl_page():
    with _db() as db:
        force_intern_keyword = SettingsService(db).get_force_intern_keyword()
    return render_template("crawl.html", force_intern_keyword=force_intern_keyword)


@bp.get("/settings")
@login_required
def settings():
    site_keys = (
        [f"public:{k}" for k in SITES["PUBLIC"].keys()]
        + [f"internship:{k}" for k in SITES["INTERNSHIP"].keys()]
        + [f"public_sector:{k}" for k in SITES["PUBLIC_SECTOR"].keys()]
    )
    with _db() as db:
        svc = SettingsService(db)
        svc.ensure_sites(site_keys)
        keywords = svc.get_keywords()
        site_enabled = svc.get_site_enabled_map()
        force_intern_keyword = svc.get_force_intern_keyword()

    return render_template(
        "settings.html",
        keywords="\n".join(keywords),
        site_keys=site_keys,
        site_enabled=site_enabled,
        force_intern_keyword=force_intern_keyword,
    )


@bp.post("/settings/keywords")
@login_required
def settings_keywords():
    raw = (request.form.get("keywords") or "").strip()
    keywords = [line.strip() for line in raw.splitlines() if line.strip()]
    with _db() as db:
        SettingsService(db).set_keywords(keywords)
    flash("키워드를 저장했습니다.", "success")
    return redirect(url_for("web.settings"))


@bp.post("/settings/sites")
@login_required
def settings_sites():
    enabled = set(request.form.getlist("enabled"))
    site_keys = (
        [f"public:{k}" for k in SITES["PUBLIC"].keys()]
        + [f"internship:{k}" for k in SITES["INTERNSHIP"].keys()]
        + [f"public_sector:{k}" for k in SITES["PUBLIC_SECTOR"].keys()]
    )
    with _db() as db:
        svc = SettingsService(db)
        svc.ensure_sites(site_keys)
        for k in site_keys:
            svc.set_site_enabled(k, k in enabled)
    flash("사이트 설정을 저장했습니다.", "success")
    return redirect(url_for("web.settings"))


@bp.post("/settings/internship")
@login_required
def settings_internship():
    enabled = bool(request.form.get("force_intern_keyword"))
    with _db() as db:
        SettingsService(db).set_force_intern_keyword(enabled)
    flash("인턴 검색 옵션을 저장했습니다.", "success")
    return redirect(url_for("web.settings"))

