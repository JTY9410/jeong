from __future__ import annotations

from contextlib import asynccontextmanager
from playwright.async_api import async_playwright
from playwright.async_api import Route, Request

try:
    # playwright-stealth (python) API differs by version; support the common one.
    from playwright_stealth import stealth_async  # type: ignore
except Exception:  # pragma: no cover
    stealth_async = None


async def auto_scroll(page, *, steps: int = 6, step_px: int = 900, pause_ms: int = 700) -> None:
    """
    Best-effort infinite-scroll loader.
    Some sites load more cards as the user scrolls; this nudges the page a few times.
    """
    try:
        for _ in range(max(0, int(steps))):
            await page.mouse.wheel(0, step_px)
            await page.wait_for_timeout(pause_ms)
    except Exception:
        return


async def _route_lightweight(route: Route, request: Request) -> None:
    """
    Reduce page weight / speed up DOM readiness by blocking non-essential resources.
    Helps on sites that time out or close connections under heavy resource load.
    """
    try:
        rt = request.resource_type
        url = request.url.lower()

        if rt in {"image", "media", "font"}:
            await route.abort()
            return

        # common trackers/ads
        if any(
            s in url
            for s in (
                "doubleclick.net",
                "googletagmanager.com",
                "google-analytics.com",
                "analytics",
                "facebook.net",
                "hotjar",
                "datadog",
                "segment",
                "amplitude",
                "mixpanel",
            )
        ):
            await route.abort()
            return

        await route.continue_()
    except Exception:
        # never fail the crawl because routing failed
        try:
            await route.continue_()
        except Exception:
            return


@asynccontextmanager
async def new_stealth_page(*, lightweight: bool = False):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            locale="ko-KR",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        if lightweight:
            await context.route("**/*", _route_lightweight)
        page = await context.new_page()
        if stealth_async:
            await stealth_async(page)
        try:
            yield page
        finally:
            await context.close()
            await browser.close()

