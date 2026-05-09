from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

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
from app import csrf


bp = Blueprint("web", __name__)

# 프로세스 내 크롤 상태 (EC2 전용 — 싱글 워커 환경)
_crawl_state: dict = {"running": False, "started_at": None, "result": None, "error": None}
_crawl_lock = threading.Lock()


def _crawler_upstream_timeout_sec() -> int:
    """
    Vercel 함수는 maxDuration(예: vercel.json) 안에 응답해야 합니다.
    EC2 크롤이 오래 걸리면 urlopen이 끝나기 전에 서버리스가 강제 종료되므로,
    CRAWLER_REQUEST_TIMEOUT_SEC로 맞춥니다(미설정 시 Vercel에서는 보수적으로 짧게).
    """
    raw = (os.getenv("CRAWLER_REQUEST_TIMEOUT_SEC") or "").strip()
    if raw:
        try:
            return max(5, min(int(float(raw)), 900))
        except ValueError:
            pass
    # vercel.json maxDuration(초) 안에서 EC2 응답을 기다립니다. 플랜 한도가 더 짧으면 CRAWLER_REQUEST_TIMEOUT_SEC 로 낮추세요.
    if os.getenv("VERCEL") or os.getenv("VERCEL_ENV"):
        return 250
    return 170


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
@csrf.exempt
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
@csrf.exempt
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
        # Vercel env 값이 CLI 입력 등으로 개행을 포함하는 경우가 있어 strip()으로 정리합니다.
        crawler_url = (os.getenv("CRAWLER_PROXY_URL") or "").strip().rstrip("/")
        secret = (os.getenv("CRAWLER_SHARED_SECRET") or "").strip()
        if not crawler_url or not secret:
            current_app.logger.warning("vercel crawl: missing CRAWLER_PROXY_URL or CRAWLER_SHARED_SECRET")
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "crawler_proxy_not_configured",
                        "message": "Vercel에서는 EC2 크롤러로 프록시합니다. CRAWLER_PROXY_URL 과 CRAWLER_SHARED_SECRET 을 설정하세요.",
                    }
                ),
                500,
            )

        # EC2에 크롤 시작만 요청하고 즉시 반환 (비동기 kick).
        # Vercel 함수 타임아웃(10초) 문제를 근본적으로 해결합니다.
        host = urlparse(crawler_url).netloc or crawler_url
        upstream = f"{crawler_url}/internal/crawl/run"
        payload = json.dumps({"force_intern_keyword": bool(force_intern_keyword)}).encode("utf-8")
        req = Request(upstream, data=payload, method="POST",
                      headers={"Content-Type": "application/json",
                                "Accept": "application/json",
                                "X-CRAWL-SECRET": secret})
        try:
            # 타임아웃을 짧게(8초) 줘서 Vercel 10초 한도 안에 반환
            with urlopen(req, timeout=8) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(body) if body else {}
            current_app.logger.info("vercel crawl kick ok host=%s status=%s",
                                    host, data.get("status"))
            return jsonify({"ok": True, "status": data.get("status", "started")}), 202
        except HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            return jsonify({"ok": False, "error": "crawler_http_error",
                            "message": f"크롤러 HTTP 오류: {e.code}",
                            "status": e.code, "raw": raw[:2000]}), 502
        except (URLError, OSError) as e:
            return jsonify({"ok": False, "error": "crawler_unreachable",
                            "message": f"크롤러에 연결할 수 없습니다: {e}"}), 502
        except Exception as e:
            current_app.logger.exception("vercel crawl kick failed host=%s", host)
            return jsonify({"ok": False, "error": "crawler_request_failed",
                            "message": str(e)}), 502

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
@csrf.exempt
def internal_run_crawl():
    """
    EC2 전용. 크롤을 백그라운드 스레드에서 실행하고 즉시 반환합니다.
    Vercel 함수 타임아웃(10초)을 피하기 위해 비동기 방식으로 동작합니다.
    """
    secret = (os.getenv("CRAWLER_SHARED_SECRET") or "").strip()
    if not secret or (request.headers.get("X-CRAWL-SECRET") or "").strip() != secret:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    force_intern_keyword = bool(data.get("force_intern_keyword", True))

    with _crawl_lock:
        if _crawl_state["running"]:
            return jsonify({"ok": True, "status": "already_running",
                            "started_at": _crawl_state["started_at"]}), 200
        _crawl_state["running"] = True
        _crawl_state["started_at"] = datetime.utcnow().isoformat()
        _crawl_state["result"] = None
        _crawl_state["error"] = None

    app = current_app._get_current_object()

    def _run():
        try:
            mgr = app.extensions.get("scheduler_manager")
            if mgr:
                result = mgr.run_once(force_intern_keyword=force_intern_keyword)
            else:
                SessionLocal = app.extensions["SessionLocal"]
                with SessionLocal() as db:
                    result = CrawlService(db).run(force_intern_keyword=force_intern_keyword)
            with _crawl_lock:
                _crawl_state["result"] = result
        except Exception as e:
            with _crawl_lock:
                _crawl_state["error"] = str(e)
        finally:
            with _crawl_lock:
                _crawl_state["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "status": "started",
                    "started_at": _crawl_state["started_at"]}), 202


@bp.get("/internal/crawl/status")
@csrf.exempt
def internal_crawl_status():
    """EC2 크롤 진행 상태 조회. Vercel 폴링용."""
    secret = (os.getenv("CRAWLER_SHARED_SECRET") or "").strip()
    if not secret or (request.headers.get("X-CRAWL-SECRET") or "").strip() != secret:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    with _crawl_lock:
        state = dict(_crawl_state)

    if state["running"]:
        return jsonify({"ok": True, "status": "running",
                        "started_at": state["started_at"]}), 200
    if state["error"]:
        return jsonify({"ok": False, "status": "error",
                        "error": state["error"]}), 200
    if state["result"] is not None:
        return jsonify({"ok": True, "status": "done",
                        "result": state["result"]}), 200
    return jsonify({"ok": True, "status": "idle"}), 200


@bp.get("/api/crawl/status")
@csrf.exempt
@api_limiter
@api_login_required
def api_crawl_status():
    """크롤 진행 상태 조회. Vercel에서는 EC2로 프록시."""
    if os.getenv("VERCEL") or os.getenv("VERCEL_ENV"):
        crawler_url = (os.getenv("CRAWLER_PROXY_URL") or "").strip().rstrip("/")
        secret = (os.getenv("CRAWLER_SHARED_SECRET") or "").strip()
        if not crawler_url or not secret:
            return jsonify({"ok": True, "status": "idle"}), 200
        try:
            req = Request(f"{crawler_url}/internal/crawl/status",
                          headers={"Accept": "application/json",
                                   "X-CRAWL-SECRET": secret})
            with urlopen(req, timeout=8) as resp:
                return jsonify(json.loads(resp.read().decode("utf-8", errors="replace"))), 200
        except Exception:
            return jsonify({"ok": True, "status": "idle"}), 200

    # EC2 직접 실행 시
    with _crawl_lock:
        state = dict(_crawl_state)
    if state["running"]:
        return jsonify({"ok": True, "status": "running",
                        "started_at": state["started_at"]}), 200
    if state["error"]:
        return jsonify({"ok": False, "status": "error", "error": state["error"]}), 200
    if state["result"] is not None:
        return jsonify({"ok": True, "status": "done", "result": state["result"]}), 200
    return jsonify({"ok": True, "status": "idle"}), 200


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

