# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""
Shared SUMO (support.mozilla.org) browser helper.

Drives a real Chromium (Playwright) so the JavaScript challenge that fronts the
API is satisfied, then lets callers fetch JSON API pages from inside the
browser's authenticated context. Proven in Bucket 0 (poc.py) to work both
headed and headless.
"""

import csv
import random
import sys
import time

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError


def _max_csv_field_size():
    """Always allow maximum-size CSV fields (question/answer content can be very
    large). Set here so every module that imports sumo inherits it."""
    limit = sys.maxsize
    while True:
        try:
            csv.field_size_limit(limit)
            return
        except OverflowError:
            limit //= 10


_max_csv_field_size()

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

    def __init__(self, headless=False, settle_ms=6000,
                 max_attempts=5, backoff_base=2.0,
                 retry_jitter_min_s=60, retry_jitter_max_s=300):
        self.headless = headless
        self.settle_ms = settle_ms
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        # Extra random pause added on top of a 429 wait (default 1-5 min) so we
        # always retry strictly AFTER SUMO's Retry-After window and desync retries.
        self.retry_jitter_min_s = retry_jitter_min_s
        self.retry_jitter_max_s = retry_jitter_max_s
        self._pw = None
        self._browser = None
        self._page = None

    def __enter__(self):
        self._pw = sync_playwright().start()
        # --disable-quic: SUMO's CDN advertises HTTP/3, and Chromium can stall
        # indefinitely on the QUIC/UDP path on some networks (while curl over
        # TCP is fine); forcing HTTP over TCP avoids that hang.
        self._browser = self._pw.chromium.launch(
            headless=self.headless, args=["--disable-quic"])
        context = self._browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            viewport={"width": 1280, "height": 800},
        )
        # Acquire challenge cookies by loading the site once. This load can
        # transiently stall (SUMO intermittently tarpits automated clients), so
        # retry with a fresh page + backoff rather than letting one timeout kill
        # the whole per-day scrape — a sustained stall window otherwise fails
        # every day in a backfill (the "cascade" failures seen in practice).
        last_err = None
        for attempt in range(1, self.max_attempts + 1):
            self._page = context.new_page()
            try:
                self._page.goto(HOME_URL, wait_until="domcontentloaded",
                                timeout=60000)
                self._page.wait_for_timeout(self.settle_ms)
                return self
            except PWTimeoutError as e:
                last_err = e
                print(f"home load timed out "
                      f"(attempt {attempt}/{self.max_attempts})",
                      file=sys.stderr, flush=True)
                self._page.close()
                if attempt < self.max_attempts:
                    time.sleep(self.backoff_base ** attempt)
        raise last_err

    def __exit__(self, exc_type, exc, tb):
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()
        return False

    def _raw_fetch(self, url):
        """Do one in-page fetch(); return {status, json, snippet, retry_after}."""
        return self._page.evaluate(
            """async (url) => {
                const res = await fetch(url, {
                    headers: { 'Accept': 'application/json' },
                    credentials: 'include',
                });
                const text = await res.text();
                let json = null;
                try { json = JSON.parse(text); } catch (e) {}
                return {
                    status: res.status,
                    json,
                    snippet: text.slice(0, 300),
                    retry_after: res.headers.get('retry-after'),
                };
            }""",
            url,
        )

    def fetch_json(self, url):
        """Fetch `url` via an in-page fetch(), retrying transient failures.

        Reuses the page's cookies/origin so the JS challenge stays satisfied.
        Retries with exponential backoff on HTTP 429 and 5xx (honouring
        Retry-After for 429). A 200 that isn't JSON is treated as a challenge
        hiccup and retried too. Other 4xx fail immediately. Raises RuntimeError
        once attempts are exhausted.
        """
        last = None
        for attempt in range(1, self.max_attempts + 1):
            result = self._raw_fetch(url)
            status = result["status"]
            last = result

            if status == 200 and result["json"] is not None:
                return result["json"]

            transient = status == 429 or status >= 500 or status == 200
            if not transient:
                raise RuntimeError(
                    f"HTTP {status} (non-retryable) for {url}\n"
                    f"First 300 chars: {result['snippet']!r}"
                )
            if attempt == self.max_attempts:
                break

            wait = self.backoff_base * (2 ** (attempt - 1))
            if status == 429:
                # Honour SUMO's Retry-After in full (it can be ~600s), then add
                # a random 1-5 min on top so we always retry after the server's
                # window and don't synchronise retries across calls.
                if result.get("retry_after"):
                    try:
                        wait = max(wait, float(result["retry_after"]))
                    except (TypeError, ValueError):
                        pass
                wait += random.uniform(self.retry_jitter_min_s,
                                       self.retry_jitter_max_s)
            reason = (f"HTTP {status}" if status != 200
                      else "200 but non-JSON (challenge?)")
            print(f"  retry {attempt}/{self.max_attempts - 1} after {wait:.0f}s "
                  f"({reason}) for {url}", file=sys.stderr)
            time.sleep(wait)

        raise RuntimeError(
            f"Gave up after {self.max_attempts} attempts; last status "
            f"{last['status']} for {url}\nFirst 300 chars: {last['snippet']!r}\n"
            "If this is a 200 with HTML, the browser may not have passed the "
            "challenge."
        )

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
