"""
Shared SUMO (support.mozilla.org) browser helper.

Drives a real Chromium (Playwright) so the JavaScript challenge that fronts the
API is satisfied, then lets callers fetch JSON API pages from inside the
browser's authenticated context. Proven in Bucket 0 (poc.py) to work both
headed and headless.
"""

import json
from contextlib import contextmanager

from playwright.sync_api import sync_playwright

HOME_URL = "https://support.mozilla.org/"
API_BASE = "https://support.mozilla.org/api/2/"

# A realistic, current desktop UA reduces the chance of being flagged.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class SumoBrowser:
    """A browser session that has passed the SUMO JS challenge.

    Use as a context manager:

        with SumoBrowser(headless=True) as sumo:
            data = sumo.fetch_json("https://support.mozilla.org/api/2/question/?...")
            for page in sumo.paginate(first_url):
                ...
    """

    def __init__(self, headless=False, settle_ms=6000):
        self.headless = headless
        self.settle_ms = settle_ms
        self._pw = None
        self._browser = None
        self._page = None

    def __enter__(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        context = self._browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            viewport={"width": 1280, "height": 800},
        )
        self._page = context.new_page()
        # Acquire challenge cookies by loading the site once.
        self._page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        self._page.wait_for_timeout(self.settle_ms)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()
        return False

    def fetch_json(self, url):
        """Fetch `url` via an in-page fetch() so cookies/origin are reused.

        Returns the parsed JSON. Raises RuntimeError if the response was not
        JSON (e.g. the challenge HTML came back instead).
        """
        result = self._page.evaluate(
            """async (url) => {
                const res = await fetch(url, {
                    headers: { 'Accept': 'application/json' },
                    credentials: 'include',
                });
                const text = await res.text();
                let json = null;
                try { json = JSON.parse(text); } catch (e) {}
                return { status: res.status, json, snippet: text.slice(0, 300) };
            }""",
            url,
        )
        if result["json"] is None:
            raise RuntimeError(
                f"Non-JSON response (status {result['status']}) for {url}\n"
                f"First 300 chars: {result['snippet']!r}\n"
                "The browser may not have passed the challenge."
            )
        return result["json"]

    def paginate(self, first_url, on_page=None):
        """Yield each result list across paginated pages, following `next`.

        `on_page(page_json)` is called with the raw page dict (for early-stop
        decisions); if it returns True, pagination stops after that page.
        """
        url = first_url
        while url:
            page = self.fetch_json(url)
            stop = on_page(page) if on_page else False
            yield page.get("results", [])
            if stop:
                break
            url = page.get("next")
