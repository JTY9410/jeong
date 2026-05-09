from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import os
from urllib.parse import quote, quote_plus

from app.crawling.playwright_factory import new_stealth_page, auto_scroll


logger = logging.getLogger(__name__)

def _norm_keywords(keywords: list[str]) -> list[str]:
    return [k.strip() for k in (keywords or []) if (k or "").strip()]


def _matches_any_keyword(text: str, keywords: list[str]) -> bool:
    ks = _norm_keywords(keywords)
    if not ks:
        return True
    t = (text or "").lower()
    return any(k.lower() in t for k in ks)


def _build_intern_search_terms(keywords: list[str], *, force_intern_keyword: bool = True) -> str:
    base = " ".join(_norm_keywords(keywords)).strip()
    if not base:
        base = "인턴"
    if force_intern_keyword and "인턴" not in base and "intern" not in base.lower():
        base = f"{base} 인턴"
    return base


def _is_humanities(title: str, keywords: list[str]) -> bool:
    t = (title or "").lower()
    return any((k or "").lower() in t for k in keywords)


@dataclass(frozen=True)
class ScrapeJob:
    site: str
    title: str
    company: str
    deadline: str | None
    url: str
    job_type: str | None = None
    location: str | None = None
    posted_date: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class ScrapeInternship:
    site: str
    title: str
    company: str
    start_date: str | None
    end_date: str | None
    application_deadline: str | None
    url: str
    location: str | None = None
    description: str | None = None


async def scrape_jobkorea_public(keywords: list[str]) -> list[ScrapeJob]:
    """
    요구사항의 잡코리아 예시를 참고하되, 실제 DOM은 변경될 수 있습니다.
    - 실패해도 앱 전체가 깨지지 않도록 예외를 삼켜서 빈 리스트를 반환합니다.
    """
    url = "https://www.jobkorea.co.kr/starter/calendar"
    try:
        async with new_stealth_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(800)
            await auto_scroll(page, steps=5)

            items = await page.query_selector_all(".list_job_item, .list-job-item, li")
            out: list[ScrapeJob] = []
            for it in items[:200]:
                title_el = await it.query_selector(".tit_job, .tit, .title")
                company_el = await it.query_selector(".name_company, .company, .corp")
                deadline_el = await it.query_selector(".date_deadline, .deadline")

                title = (await title_el.inner_text()) if title_el else ""
                company = (await company_el.inner_text()) if company_el else ""
                deadline = (await deadline_el.inner_text()) if deadline_el else None
                a = await it.query_selector("a[href]")
                href = await a.get_attribute("href") if a else None
                if not title or not company or not href:
                    continue
                if not _is_humanities(title, keywords):
                    continue
                full_url = href if href.startswith("http") else f"https://www.jobkorea.co.kr{href}"
                out.append(
                    ScrapeJob(
                        site="JobKorea",
                        title=title.strip(),
                        company=company.strip(),
                        deadline=(deadline.strip() if deadline else None),
                        url=full_url,
                        job_type=None,
                    )
                )
            return out
    except Exception:
        return []


async def scrape_saramin_public(keywords: list[str]) -> list[ScrapeJob]:
    """
    사람인 공개채용: 페이지/DOM이 자주 바뀌므로 다중 셀렉터로 방어적으로 수집합니다.
    """
    url = "https://www.saramin.co.kr/zf_user/jobs/public/home"
    try:
        async with new_stealth_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(800)
            await auto_scroll(page, steps=6)

            items = await page.query_selector_all(".job_item, .item_recruit, li")
            out: list[ScrapeJob] = []
            for it in items[:250]:
                title_el = await it.query_selector(".job_tit a, .job_title, a")
                company_el = await it.query_selector(".corp_name, .company_name, .company, .corp")
                deadline_el = await it.query_selector(".deadlines, .deadline, .date")

                title = (await title_el.inner_text()) if title_el else ""
                company = (await company_el.inner_text()) if company_el else ""
                deadline = (await deadline_el.inner_text()) if deadline_el else None

                href = await title_el.get_attribute("href") if title_el else None
                if not title or not company or not href:
                    continue
                if not _is_humanities(title, keywords):
                    continue

                full_url = href if href.startswith("http") else f"https://www.saramin.co.kr{href}"
                out.append(
                    ScrapeJob(
                        site="Saramin",
                        title=title.strip(),
                        company=company.strip(),
                        deadline=(deadline.strip() if deadline else None),
                        url=full_url,
                    )
                )
            return out
    except Exception:
        return []


async def scrape_wevity_internships(keywords: list[str], *, force_intern_keyword: bool = True) -> list[ScrapeInternship]:
    url = "https://www.wevity.com"
    try:
        async with new_stealth_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(800)
            await auto_scroll(page, steps=6)
            rows = await page.evaluate(
                """
                () => {
                  // Wevity는 메뉴/게시판 구조가 자주 바뀜: 링크 텍스트 기반으로 인턴/채용 후보만 1차 수집
                  const anchors = Array.from(document.querySelectorAll('a[href]'))
                    .map(a => ({ href: a.getAttribute('href') || '', text: (a.textContent || '').replace(/\\s+/g,' ').trim() }))
                    .filter(x => x.href && x.text && /(인턴|intern|채용|모집)/i.test(x.text))
                    .slice(0, 400);
                  const out = [];
                  for (const a of anchors) {
                    const full = a.href.startsWith('http') ? a.href : ('https://www.wevity.com' + (a.href.startsWith('/') ? '' : '/') + a.href);
                    out.push({ title: a.text, url: full });
                  }
                  const seen = new Set();
                  return out.filter(x => { if (seen.has(x.url)) return false; seen.add(x.url); return true; }).slice(0, 120);
                }
                """
            )

            out: list[ScrapeInternship] = []
            for r in rows:
                title = (r.get("title") or "").strip()
                full_url = (r.get("url") or "").strip()
                if not title or not full_url:
                    continue
                # 인턴 중심
                if "인턴" not in title and "intern" not in title.lower():
                    continue
                out.append(
                    ScrapeInternship(
                        site="Wevity",
                        title=title,
                        company="Wevity",
                        start_date=None,
                        end_date=None,
                        application_deadline=None,
                        url=full_url,
                    )
                )
            if _norm_keywords(keywords):
                filtered = [x for x in out if _matches_any_keyword(x.title, keywords)]
                if filtered:
                    out = filtered
            return out
    except Exception:
        logger.exception("wevity internship scrape failed")
        return []


async def scrape_jobkorea_internships(keywords: list[str], *, force_intern_keyword: bool = True) -> list[ScrapeInternship]:
    # Best-effort search page (DOM may change)
    q = quote_plus(_build_intern_search_terms(keywords, force_intern_keyword=force_intern_keyword), safe="")
    url = f"https://www.jobkorea.co.kr/Search/?stext={q}"
    try:
        async with new_stealth_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(1200)
            await auto_scroll(page, steps=7)

            rows = await page.evaluate(
                """
                () => {
                  const anchors = Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => {
                      const href = a.getAttribute('href') || '';
                      const text = (a.textContent || '').replace(/\\s+/g,' ').trim();
                      if (!href || !text) return false;
                      if (!/(인턴|intern)/i.test(text)) return false;
                      // 채용공고 상세로 이어질 확률이 높은 링크만
                      return href.includes('Recruit') || href.includes('recruit') || href.includes('GI_Read');
                    })
                    .slice(0, 250);
                  const out = [];
                  for (const a of anchors) {
                    const title = (a.textContent || '').replace(/\\s+/g,' ').trim();
                    const href = a.getAttribute('href');
                    const full = href.startsWith('http') ? href : ('https://www.jobkorea.co.kr' + href);
                    const container = a.closest('li, tr, article, section, div') || a.parentElement;
                    const cleaned = (container ? container.textContent : title).replace(/\\s+/g,' ').trim();
                    out.push({ title, url: full, text: cleaned });
                  }
                  const seen = new Set();
                  return out.filter(x => { if (seen.has(x.url)) return false; seen.add(x.url); return true; }).slice(0, 120);
                }
                """
            )

            out: list[ScrapeInternship] = []
            for r in rows:
                title = (r.get("title") or "").strip()
                full_url = (r.get("url") or "").strip()
                if not title or not full_url:
                    continue
                out.append(
                    ScrapeInternship(
                        site="JobKorea",
                        title=title,
                        company="JobKorea",
                        start_date=None,
                        end_date=None,
                        application_deadline=None,
                        url=full_url,
                    )
                )
            if _norm_keywords(keywords):
                filtered = [x for x in out if _matches_any_keyword(x.title, keywords)]
                if filtered:
                    out = filtered
            return out
    except Exception:
        logger.exception("jobkorea internship scrape failed")
        return []


async def scrape_saramin_internships(keywords: list[str], *, force_intern_keyword: bool = True) -> list[ScrapeInternship]:
    # Saramin can be heavy / can block; try lighter URLs with longer timeout.
    q = quote_plus(_build_intern_search_terms(keywords, force_intern_keyword=force_intern_keyword), safe="")
    urls = [
        f"https://www.saramin.co.kr/zf_user/search/recruit?searchword={q}",
        f"https://www.saramin.co.kr/zf_user/search?searchword={q}",
    ]
    try:
        # Saramin sometimes returns an empty TCP response (net::ERR_EMPTY_RESPONSE).
        # Retrying with a *fresh* context/page is significantly more reliable than reusing the same page.
        last_err: Exception | None = None
        page = None
        for url in urls:
            for attempt in range(3):
                try:
                    async with new_stealth_page(lightweight=True) as page:
                        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                        await page.wait_for_timeout(1400)
                        await auto_scroll(page, steps=8)
                        # Saramin may redirect into an HTTP_BAD_REQUEST refresh loop (WAF/block).
                        cur = (page.url or "").lower()
                        html = (await page.content()).lower()
                        if "http_bad_request.php" in cur or "http_bad_request.php" in html:
                            raise RuntimeError("saramin blocked: HTTP_BAD_REQUEST refresh loop")
                        last_err = None
                        break
                except Exception as e:
                    last_err = e
                    # fast-path backoff only for flaky network-ish failures
                    msg = str(e)
                    if any(
                        s in msg
                        for s in (
                            "ERR_EMPTY_RESPONSE",
                            "ERR_CONNECTION_RESET",
                            "ERR_CONNECTION_CLOSED",
                            "ERR_CONNECTION_TIMED_OUT",
                            "Timeout",
                            "timeout",
                        )
                    ):
                        # 0.8s, 1.6s, 3.2s
                        try:
                            await page.wait_for_timeout(int(800 * (2**attempt)))
                        except Exception:
                            pass
                        continue
                    break
            if last_err is None:
                break
        if last_err is not None:
            raise last_err

        rows = await page.evaluate(
                """
                () => {
                  const anchors = Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => {
                      const href = a.getAttribute('href') || '';
                      const text = (a.textContent || '').replace(/\\s+/g,' ').trim();
                      if (!href || !text) return false;
                      if (!/(인턴|intern)/i.test(text)) return false;
                      return href.includes('/zf_user/jobs/') || href.includes('/recruit/');
                    })
                    .slice(0, 250);
                  const out = [];
                  for (const a of anchors) {
                    const title = (a.textContent || '').replace(/\\s+/g,' ').trim();
                    const href = a.getAttribute('href');
                    const full = href.startsWith('http') ? href : ('https://www.saramin.co.kr' + href);
                    out.push({ title, url: full });
                  }
                  const seen = new Set();
                  return out.filter(x => { if (seen.has(x.url)) return false; seen.add(x.url); return true; }).slice(0, 120);
                }
                """
        )

        out: list[ScrapeInternship] = []
        for r in rows:
            title = (r.get("title") or "").strip()
            full_url = (r.get("url") or "").strip()
            if not title or not full_url:
                continue
            out.append(
                ScrapeInternship(
                    site="Saramin",
                    title=title,
                    company="Saramin",
                    start_date=None,
                    end_date=None,
                    application_deadline=None,
                    url=full_url,
                )
            )
        return out
    except Exception:
        logger.exception("saramin internship scrape failed")
        return []


async def scrape_linkareer_internships(keywords: list[str], *, force_intern_keyword: bool = True) -> list[ScrapeInternship]:
    url = "https://linkareer.com/list/intern"
    try:
        async with new_stealth_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=40_000)
            await page.wait_for_timeout(900)
            await auto_scroll(page, steps=7)

            rows = await page.evaluate(
                """
                () => {
                  // Linkareer list pages: anchor hrefs often look like /activity/<id>
                  const anchors = Array.from(document.querySelectorAll('a[href^="/activity/"]')).slice(0, 500);
                  const out = [];
                  for (const a of anchors) {
                    const title = (a.textContent || "").replace(/\\s+/g," ").trim();
                    const href = a.getAttribute("href") || "";
                    if (!title || !href) continue;
                    if (!/(인턴|intern)/i.test(title)) continue;
                    const full = "https://linkareer.com" + href;
                    const container = a.closest("tr, li, div") || a.parentElement;
                    const text = (container ? container.textContent : title).replace(/\\s+/g," ").trim();
                    let company = "";
                    if (container) {
                      const companyEl = container.querySelector("strong, .company, .corp, .name, .recruit_company") || null;
                      company = companyEl ? (companyEl.textContent || "").trim() : "";
                    }
                    if (!company) {
                      const m = title.match(/^\\[(.+?)\\]\\s*/);
                      if (m) company = m[1].trim();
                    }
                    let deadline = null;
                    const dm = text.match(/~\\s*\\d{2}\\.\\d{2}|채용\\s*시\\s*마감|상시|예정/);
                    if (dm) deadline = dm[0];
                    out.push({ title, url: full, company, deadline });
                  }
                  const seen = new Set();
                  return out.filter(x => { if (seen.has(x.url)) return false; seen.add(x.url); return true; }).slice(0, 140);
                }
                """
            )

            out: list[ScrapeInternship] = []
            for r in rows:
                title = (r.get("title") or "").strip()
                full_url = (r.get("url") or "").strip()
                if not title or not full_url:
                    continue
                out.append(
                    ScrapeInternship(
                        site="Linkareer",
                        title=title,
                        company=((r.get("company") or "").strip() or "Linkareer"),
                        start_date=None,
                        end_date=None,
                        application_deadline=(r.get("deadline") or None),
                        url=full_url,
                    )
                )
            if _norm_keywords(keywords):
                filtered = [x for x in out if _matches_any_keyword(f"{x.title} {x.company}", keywords)]
                if filtered:
                    out = filtered
            return out
    except Exception:
        logger.exception("linkareer internship scrape failed")
        return []


async def scrape_catch_internships(keywords: list[str], *, force_intern_keyword: bool = True) -> list[ScrapeInternship]:
    # Catch listing pages vary; use RecruitSearch and filter "인턴" in link text.
    url = "https://www.catch.co.kr/NCS/RecruitSearch"
    try:
        async with new_stealth_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=40_000)
            await page.wait_for_timeout(1200)
            await auto_scroll(page, steps=7)

            rows = await page.evaluate(
                """
                () => {
                  const anchors = Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => {
                      const href = a.getAttribute('href') || '';
                      const text = (a.textContent || '').replace(/\\s+/g,' ').trim();
                      if (!href || !text) return false;
                      if (!/(인턴|intern)/i.test(text)) return false;
                      return href.toLowerCase().includes('recruit');
                    })
                    .slice(0, 500);
                  const out = [];
                  for (const a of anchors) {
                    const title = (a.textContent || '').replace(/\\s+/g,' ').trim();
                    const href = a.getAttribute('href');
                    const full = href.startsWith('http') ? href : ('https://www.catch.co.kr' + href);
                    const container = a.closest('tr, li, .recruit, .list, .item, div') || a.parentElement;
                    const cleaned = (container ? container.textContent : title).replace(/\\s+/g,' ').trim();
                    let company = '';
                    if (container) {
                      const companyEl = container.querySelector('.company, .corp, .name, strong') || null;
                      company = companyEl ? (companyEl.textContent || '').trim() : '';
                    }
                    let deadline = null;
                    const dm = cleaned.match(/~\\s*\\d{2}\\.\\d{2}|채용\\s*시\\s*마감|상시|예정/);
                    if (dm) deadline = dm[0];
                    out.push({ title, url: full, company, deadline });
                  }
                  const seen = new Set();
                  return out.filter(x => { if (seen.has(x.url)) return false; seen.add(x.url); return true; }).slice(0, 140);
                }
                """
            )

            out: list[ScrapeInternship] = []
            for r in rows:
                title = (r.get("title") or "").strip()
                full_url = (r.get("url") or "").strip()
                if not title or not full_url:
                    continue
                out.append(
                    ScrapeInternship(
                        site="Catch",
                        title=title,
                        company=((r.get("company") or "").strip() or "Catch"),
                        start_date=None,
                        end_date=None,
                        application_deadline=(r.get("deadline") or None),
                        url=full_url,
                    )
                )
            if _norm_keywords(keywords):
                filtered = [x for x in out if _matches_any_keyword(f"{x.title} {x.company}", keywords)]
                if filtered:
                    out = filtered
            return out
    except Exception:
        logger.exception("catch internship scrape failed")
        return []


async def scrape_linkedin_internships(keywords: list[str]) -> list[ScrapeInternship]:
    # LinkedIn is often blocked behind auth/anti-bot; keep best-effort and do not fail crawl.
    logger.warning("linkedin internship scrape skipped (likely requires login/anti-bot)")
    return []

async def scrape_incruit_internships(keywords: list[str], *, force_intern_keyword: bool = True) -> list[ScrapeInternship]:
    # Incruit search landing tends to change; do best-effort anchor collection.
    q = quote_plus(_build_intern_search_terms(keywords, force_intern_keyword=force_intern_keyword), safe="")
    url = f"https://job.incruit.com/jobdb_list/searchjob.asp?col=job&query={q}"
    try:
        async with new_stealth_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(1200)
            await auto_scroll(page, steps=7)

            rows = await page.evaluate(
                """
                () => {
                  const anchors = Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => {
                      const href = a.getAttribute('href') || '';
                      const text = (a.textContent || '').replace(/\\s+/g,' ').trim();
                      if (!href || !text) return false;
                      if (!/(인턴|intern)/i.test(text)) return false;
                      return href.includes('jobdb_info') || href.includes('jobdb_list') || href.includes('recruit');
                    })
                    .slice(0, 250);
                  const out = [];
                  for (const a of anchors) {
                    const title = (a.textContent || '').replace(/\\s+/g,' ').trim();
                    const href = a.getAttribute('href');
                    const full = href.startsWith('http') ? href : ('https://job.incruit.com' + (href.startsWith('/') ? '' : '/') + href);
                    out.push({ title, url: full });
                  }
                  const seen = new Set();
                  return out.filter(x => { if (seen.has(x.url)) return false; seen.add(x.url); return true; }).slice(0, 120);
                }
                """
            )

            out: list[ScrapeInternship] = []
            for r in rows:
                title = (r.get("title") or "").strip()
                full_url = (r.get("url") or "").strip()
                if not title or not full_url:
                    continue
                out.append(
                    ScrapeInternship(
                        site="Incruit",
                        title=title,
                        company="Incruit",
                        start_date=None,
                        end_date=None,
                        application_deadline=None,
                        url=full_url,
                    )
                )
            if _norm_keywords(keywords):
                filtered = [x for x in out if _matches_any_keyword(x.title, keywords)]
                if filtered:
                    out = filtered
            return out
    except Exception:
        logger.exception("incruit internship scrape failed")
        return []


async def scrape_worknet_internships(keywords: list[str], *, force_intern_keyword: bool = True) -> list[ScrapeInternship]:
    # WorkNet pages can be heavy; best-effort only.
    q = quote_plus(_build_intern_search_terms(keywords, force_intern_keyword=force_intern_keyword), safe="")
    url = f"https://www.work.go.kr/empInfo/empInfoSrch/list/dtlEmpSrchList.do?searchKeyword={q}"
    try:
        async with new_stealth_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(1500)
            await auto_scroll(page, steps=7)

            rows = await page.evaluate(
                """
                () => {
                  const anchors = Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => {
                      const href = a.getAttribute('href') || '';
                      const text = (a.textContent || '').replace(/\\s+/g,' ').trim();
                      if (!href || !text) return false;
                      if (!/(인턴|intern)/i.test(text)) return false;
                      return true;
                    })
                    .slice(0, 250);
                  const out = [];
                  for (const a of anchors) {
                    const title = (a.textContent || '').replace(/\\s+/g,' ').trim();
                    const href = a.getAttribute('href');
                    const full = href.startsWith('http') ? href : ('https://www.work.go.kr' + href);
                    out.push({ title, url: full });
                  }
                  const seen = new Set();
                  return out.filter(x => { if (seen.has(x.url)) return false; seen.add(x.url); return true; }).slice(0, 120);
                }
                """
            )

            out: list[ScrapeInternship] = []
            for r in rows:
                title = (r.get("title") or "").strip()
                full_url = (r.get("url") or "").strip()
                if not title or not full_url:
                    continue
                out.append(
                    ScrapeInternship(
                        site="WorkNet",
                        title=title,
                        company="WorkNet",
                        start_date=None,
                        end_date=None,
                        application_deadline=None,
                        url=full_url,
                    )
                )
            if _norm_keywords(keywords):
                filtered = [x for x in out if _matches_any_keyword(x.title, keywords)]
                if filtered:
                    out = filtered
            return out
    except Exception:
        logger.exception("worknet internship scrape failed")
        return []

async def scrape_interninmeta_internships(keywords: list[str], *, force_intern_keyword: bool = True) -> list[ScrapeInternship]:
    """
    interninmeta: WebFetch 타임아웃/차단 가능성이 있어 Playwright 기반으로 접근합니다.
    - 현재 사이트 성격상 "인턴 공고"가 별도 게시판/노션 스타일일 수 있어,
      1차로는 페이지 내 링크/카드 텍스트에서 공고 후보를 추출합니다.
    """
    url = "https://www.interninmeta.or.kr"
    try:
        async with new_stealth_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(1200)
            await auto_scroll(page, steps=6)

            rows = await page.evaluate(
                """
                () => {
                  const anchors = Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => {
                      const href = (a.getAttribute('href') || '');
                      if (!href) return false;
                      // interninmeta는 개별 페이지가 랜덤한 path로 존재하는 경우가 많음
                      if (href.startsWith('http') && !href.includes('interninmeta.or.kr')) return false;
                      return true;
                    })
                    .slice(0, 400);

                  const out = [];
                  for (const a of anchors) {
                    const title = (a.textContent || '').replace(/\\s+/g, ' ').trim();
                    const href = a.getAttribute('href');
                    if (!href || !title) continue;
                    const full = href.startsWith('http') ? href : ('https://www.interninmeta.or.kr' + href);

                    const container = a.closest('li, tr, article, section, div') || a.parentElement;
                    const text = (container ? container.textContent : title).replace(/\\s+/g, ' ').trim();

                    // 마감/기간 추정 (예: 2026.01.12 ~ 02.01)
                    let deadline = null;
                    const dm = text.match(/~\\s*\\d{2}\\.\\d{2}|~\\s*\\d{4}\\.\\d{2}\\.\\d{2}|마감|채용\\s*시\\s*마감/);
                    if (dm) deadline = dm[0];

                    out.push({ title, url: full, deadline });
                  }

                  // de-dupe by url
                  const seen = new Set();
                  return out.filter(x => { if (seen.has(x.url)) return false; seen.add(x.url); return true; });
                }
                """
            )

            out: list[ScrapeInternship] = []
            for r in rows:
                title = (r.get("title") or "").strip()
                full_url = (r.get("url") or "").strip()
                if not title or not full_url:
                    continue
                # 인턴 공고/모집 관련 키워드가 있는 항목 우선
                if not any(k in title.lower() for k in ["인턴", "intern", "모집", "채용"]):
                    continue
                # 인턴은 “키워드로 제외”하면 데이터가 0이 되는 경우가 많아
                # 인문계열 키워드는 알림/후처리로 활용하고 수집 자체는 막지 않습니다.

                out.append(
                    ScrapeInternship(
                        site="InternInMeta",
                        title=title,
                        company="InternInMeta",
                        start_date=None,
                        end_date=None,
                        application_deadline=(r.get("deadline") or None),
                        url=full_url,
                    )
                )
            if _norm_keywords(keywords):
                filtered = [x for x in out if _matches_any_keyword(x.title, keywords)]
                if filtered:
                    out = filtered
            return out
    except Exception:
        logger.exception("interninmeta internship scrape failed")
        return []


async def scrape_indeed_kr_internships(keywords: list[str], *, force_intern_keyword: bool = True) -> list[ScrapeInternship]:
    """
    Indeed KR:
    - 네트워크/API는 비공개/변동 가능성이 높아, Playwright DOM 파싱으로 최소 동작 구현
    - 로케일/쿼리에 따라 결과가 달라질 수 있어 "인턴" + 사용자 키워드 일부로 검색합니다.
    """
    # 검색어가 너무 길면 차단/오류 확률이 높아 상위 몇 개만 사용
    seed = [k for k in keywords if k and len(k) <= 10][:5]
    # 인턴 + 키워드 조합 (공백 검색)
    prefix = "인턴 " if force_intern_keyword else ""
    q_raw = prefix + (" ".join(seed) if seed else "")
    q = quote_plus(q_raw, safe="")
    url = f"https://kr.indeed.com/jobs?q={q}&sort=date"

    try:
        async with new_stealth_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(1200)
            await auto_scroll(page, steps=7)

            rows = await page.evaluate(
                """
                () => {
                  // Indeed는 카드 구조가 바뀌므로 a[href] 기반으로 방어적으로 수집
                  const anchors = Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => {
                      const href = a.getAttribute('href') || '';
                      return href.includes('/viewjob') || href.includes('jk=');
                    })
                    .slice(0, 200);

                  const out = [];
                  for (const a of anchors) {
                    const href = a.getAttribute('href');
                    const title = (a.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (!href || !title) continue;

                    const card = a.closest('div, article, li') || a.parentElement;
                    const text = (card ? card.textContent : title).replace(/\\s+/g, ' ').trim();

                    let company = '';
                    if (card) {
                      const companyEl =
                        card.querySelector('[data-testid="company-name"], .companyName, .company, strong') || null;
                      company = companyEl ? (companyEl.textContent || '').trim() : '';
                    }

                    let location = null;
                    if (card) {
                      const locEl =
                        card.querySelector('[data-testid="text-location"], .companyLocation, .location') || null;
                      location = locEl ? (locEl.textContent || '').trim() : null;
                    }

                    // "마감"은 Indeed에 명시적으로 없을 수 있어, 업로드/게시일 같은 정보만 있을 때는 null
                    let deadline = null;
                    const dm = text.match(/\\d+\\s*일\\s*전|방금\\s*게시됨|오늘\\s*게시됨/);
                    if (dm) deadline = dm[0];

                    out.push({ href, title, company, location, deadline });
                  }

                  const seen = new Set();
                  return out.filter(x => {
                    const key = x.href;
                    if (seen.has(key)) return false;
                    seen.add(key);
                    return true;
                  });
                }
                """
            )

            out: list[ScrapeInternship] = []
            for r in rows:
                title = (r.get("title") or "").strip()
                href = (r.get("href") or "").strip()
                if not title or not href:
                    continue

                # 인턴 중심 수집
                if "인턴" not in title and "intern" not in title.lower():
                    continue
                # 인턴 수집은 키워드로 제외하지 않음 (0건 방지)

                full_url = href if href.startswith("http") else f"https://kr.indeed.com{href}"
                out.append(
                    ScrapeInternship(
                        site="IndeedKR",
                        title=title,
                        company=((r.get("company") or "").strip() or "Indeed"),
                        start_date=None,
                        end_date=None,
                        application_deadline=(r.get("deadline") or None),
                        url=full_url,
                        location=(r.get("location") or None),
                    )
                )

            return out
    except Exception:
        logger.exception("indeed internship scrape failed")
        return []
async def scrape_linkareer_public(keywords: list[str]) -> list[ScrapeJob]:
    """
    링크: https://linkareer.com/list/recruit
    - 서버 렌더링 목록 페이지이므로 DOM 파싱으로 안정적으로 1차 수집 가능
    - DOM이 바뀌어도 앱이 죽지 않도록 예외 시 빈 리스트 반환
    """
    url = "https://linkareer.com/list/recruit"
    try:
        async with new_stealth_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(800)
            await auto_scroll(page, steps=5)

            rows = await page.evaluate(
                """
                () => {
                  const anchors = Array.from(document.querySelectorAll('a[href^="/activity/"]')).slice(0, 300);
                  const out = [];
                  for (const a of anchors) {
                    const title = (a.textContent || "").trim();
                    const href = a.getAttribute("href");
                    if (!title || !href) continue;

                    // best-effort: 주변 텍스트에서 회사/형태/지역/마감 추정
                    const container = a.closest("tr, li, div") || a.parentElement;
                    const text = (container ? container.textContent : a.textContent) || "";
                    const cleaned = text.replace(/\\s+/g, " ").trim();

                    // heuristic: 회사명은 타이틀 앞쪽 별도 컬럼/텍스트로 존재하는 경우가 많음
                    let company = "";
                    if (container) {
                      const companyEl =
                        container.querySelector("strong, .company, .corp, .name, .recruit_company") ||
                        null;
                      company = companyEl ? (companyEl.textContent || "").trim() : "";
                    }

                    // fallback: 타이틀이 [회사]로 시작하는 경우가 있어 추출 시도
                    if (!company) {
                      const m = title.match(/^\\[(.+?)\\]\\s*/);
                      if (m) company = m[1].trim();
                    }

                    // deadline: "~ 05.13" 같은 패턴이 목록에 존재
                    let deadline = null;
                    const dm = cleaned.match(/~\\s*\\d{2}\\.\\d{2}|채용\\s*시\\s*마감|예정/);
                    if (dm) deadline = dm[0];

                    // job_type: "인턴/신입/경력/계약직" 등의 텍스트가 같이 나오는 경우
                    let job_type = null;
                    const tm = cleaned.match(/인턴|신입|경력직|경력|계약직|정규직/);
                    if (tm) job_type = tm[0];

                    // location: "서울/경기/해외" 등의 단어를 단순 추정
                    let location = null;
                    const lm = cleaned.match(/서울|경기|인천|대전|대구|부산|광주|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주|해외/);
                    if (lm) location = lm[0];

                    out.push({
                      title,
                      href,
                      company,
                      deadline,
                      job_type,
                      location
                    });
                  }
                  return out;
                }
                """
            )

            out: list[ScrapeJob] = []
            for r in rows:
                title = (r.get("title") or "").strip()
                company = (r.get("company") or "").strip() or "링커리어"
                href = (r.get("href") or "").strip()
                if not title or not href:
                    continue
                if not _is_humanities(title, keywords):
                    continue
                full_url = href if href.startswith("http") else f"https://linkareer.com{href}"
                out.append(
                    ScrapeJob(
                        site="Linkareer",
                        title=title,
                        company=company,
                        deadline=(r.get("deadline") or None),
                        url=full_url,
                        job_type=(r.get("job_type") or None),
                        location=(r.get("location") or None),
                    )
                )
            return out
    except Exception:
        return []


async def scrape_catch_public(keywords: list[str]) -> list[ScrapeJob]:
    """
    캐치: https://www.catch.co.kr/NCS/RecruitSearch
    - WebFetch로는 타임아웃이 날 수 있어 Playwright DOM 파싱으로 수집
    """
    url = "https://www.catch.co.kr/NCS/RecruitSearch"
    try:
        async with new_stealth_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(1200)
            await auto_scroll(page, steps=6)

            rows = await page.evaluate(
                """
                () => {
                  const anchors = Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => (a.getAttribute('href') || '').includes('Recruit'))
                    .slice(0, 500);
                  const out = [];
                  for (const a of anchors) {
                    const href = a.getAttribute('href');
                    const title = (a.textContent || '').replace(/\\s+/g, ' ').trim();
                    if (!href || !title) continue;
                    const container = a.closest('tr, li, .recruit, .list, .item, div') || a.parentElement;
                    const text = (container ? container.textContent : a.textContent) || '';
                    const cleaned = text.replace(/\\s+/g, ' ').trim();

                    let company = '';
                    if (container) {
                      const companyEl =
                        container.querySelector('.company, .corp, .name, strong') || null;
                      company = companyEl ? (companyEl.textContent || '').trim() : '';
                    }

                    let deadline = null;
                    const dm = cleaned.match(/~\\s*\\d{2}\\.\\d{2}|채용\\s*시\\s*마감|상시|예정/);
                    if (dm) deadline = dm[0];

                    out.push({ href, title, company, deadline });
                  }
                  // de-dupe by href
                  const seen = new Set();
                  return out.filter(x => { if (seen.has(x.href)) return false; seen.add(x.href); return true; });
                }
                """
            )

            out: list[ScrapeJob] = []
            for r in rows:
                title = (r.get("title") or "").strip()
                href = (r.get("href") or "").strip()
                if not title or not href:
                    continue
                if not _is_humanities(title, keywords):
                    continue
                full_url = href if href.startswith("http") else f"https://www.catch.co.kr{href}"
                out.append(
                    ScrapeJob(
                        site="Catch",
                        title=title,
                        company=((r.get("company") or "").strip() or "캐치"),
                        deadline=(r.get("deadline") or None),
                        url=full_url,
                    )
                )
            return out
    except Exception:
        return []


async def scrape_public_sector(keywords: list[str]) -> list[ScrapeJob]:
    """
    공공/공기업:
    - 사이트 직접 스크래핑은 차단/503이 잦아, 가능하면 공공데이터포털 API 기반 수집을 우선 지원합니다.
    - `DATA_GO_KR_SERVICE_KEY` + `DATA_GO_KR_PUBLIC_RECRUIT_URL` 환경변수 설정 시 동작합니다.
    """
    try:
        from app.crawling.public_data_portal import fetch_public_jobs_from_data_go_kr

        items = fetch_public_jobs_from_data_go_kr()
        out: list[ScrapeJob] = []
        for it in items:
            title = it.title
            if not _is_humanities(title, keywords):
                continue
            out.append(
                ScrapeJob(
                    site="PublicSector",
                    title=title,
                    company=it.organization,
                    deadline=it.deadline,
                    url=it.url or "",
                )
            )
        # URL이 없는 항목은 저장 불가(유니크키가 URL)라 제외
        return [x for x in out if x.url]
    except Exception:
        return []


async def scrape_all(
    *,
    keywords: list[str],
    enabled: dict[str, bool] | None = None,
    force_intern_keyword: bool = True,
) -> tuple[list[ScrapeJob], list[ScrapeInternship]]:
    enabled = enabled or {}

    # EC2 t3.micro 등 소형 인스턴스에서는 Chromium 컨텍스트를 동시에 많이 띄우면
    # CPU/메모리/네트워크가 포화되어 서버 자체가 응답 불능(SSH/HTTP timeout)으로 빠질 수 있습니다.
    # 기본 동시성은 낮게 유지합니다.
    sem = asyncio.Semaphore(int(os.getenv("CRAWL_CONCURRENCY", "2")))

    async def _with_timeout(coro, *, timeout_sec: int, name: str):
        try:
            async with sem:
                return await asyncio.wait_for(coro, timeout=timeout_sec)
        except asyncio.TimeoutError:
            logger.warning("scrape timeout: %s (%ss)", name, timeout_sec)
            return []
        except Exception:
            logger.exception("scrape failed: %s", name)
            return []

    # 병렬 수집으로 전체 소요시간을 줄이고, 사이트별 타임아웃으로 무한 대기를 방지합니다.
    job_tasks = []
    if enabled.get("public:jobkorea", True):
        job_tasks.append(_with_timeout(scrape_jobkorea_public(keywords), timeout_sec=70, name="public:jobkorea"))
    if enabled.get("public:saramin", True):
        job_tasks.append(_with_timeout(scrape_saramin_public(keywords), timeout_sec=70, name="public:saramin"))
    if enabled.get("public:linkareer", True):
        job_tasks.append(_with_timeout(scrape_linkareer_public(keywords), timeout_sec=70, name="public:linkareer"))
    if enabled.get("public:catch", True):
        job_tasks.append(_with_timeout(scrape_catch_public(keywords), timeout_sec=80, name="public:catch"))
    if (
        enabled.get("public_sector:public_corps", True)
        or enabled.get("public_sector:govt_jobs", True)
        or enabled.get("public_sector:local_govt", True)
    ):
        job_tasks.append(_with_timeout(scrape_public_sector(keywords), timeout_sec=35, name="public_sector"))

    intern_tasks = []
    if enabled.get("internship:jobkorea", True):
        intern_tasks.append(
            _with_timeout(
                scrape_jobkorea_internships(keywords, force_intern_keyword=force_intern_keyword),
                timeout_sec=90,
                name="internship:jobkorea",
            )
        )
    if enabled.get("internship:saramin", True):
        intern_tasks.append(
            _with_timeout(
                scrape_saramin_internships(keywords, force_intern_keyword=force_intern_keyword),
                timeout_sec=110,
                name="internship:saramin",
            )
        )
    if enabled.get("internship:incruit", True):
        intern_tasks.append(
            _with_timeout(
                scrape_incruit_internships(keywords, force_intern_keyword=force_intern_keyword),
                timeout_sec=80,
                name="internship:incruit",
            )
        )
    if enabled.get("internship:worknet", True):
        intern_tasks.append(
            _with_timeout(
                scrape_worknet_internships(keywords, force_intern_keyword=force_intern_keyword),
                timeout_sec=80,
                name="internship:worknet",
            )
        )
    if enabled.get("internship:linkareer", True):
        intern_tasks.append(
            _with_timeout(
                scrape_linkareer_internships(keywords, force_intern_keyword=force_intern_keyword),
                timeout_sec=80,
                name="internship:linkareer",
            )
        )
    if enabled.get("internship:catch", True):
        intern_tasks.append(
            _with_timeout(
                scrape_catch_internships(keywords, force_intern_keyword=force_intern_keyword),
                timeout_sec=90,
                name="internship:catch",
            )
        )
    if enabled.get("internship:linkedin", False):
        intern_tasks.append(_with_timeout(scrape_linkedin_internships(keywords), timeout_sec=15, name="internship:linkedin"))
    if enabled.get("internship:wevity", True):
        intern_tasks.append(
            _with_timeout(
                scrape_wevity_internships(keywords, force_intern_keyword=force_intern_keyword),
                timeout_sec=80,
                name="internship:wevity",
            )
        )
    if enabled.get("internship:interninmeta", True):
        intern_tasks.append(
            _with_timeout(
                scrape_interninmeta_internships(keywords, force_intern_keyword=force_intern_keyword),
                timeout_sec=80,
                name="internship:interninmeta",
            )
        )
    if enabled.get("internship:indeed_kr", True):
        intern_tasks.append(
            _with_timeout(
                scrape_indeed_kr_internships(keywords, force_intern_keyword=force_intern_keyword),
                timeout_sec=80,
                name="internship:indeed_kr",
            )
        )

    job_results, intern_results = await asyncio.gather(
        asyncio.gather(*job_tasks, return_exceptions=False),
        asyncio.gather(*intern_tasks, return_exceptions=False),
    )

    jobs: list[ScrapeJob] = [x for sub in job_results for x in (sub or [])]
    interns: list[ScrapeInternship] = [x for sub in intern_results for x in (sub or [])]
    return jobs, interns

